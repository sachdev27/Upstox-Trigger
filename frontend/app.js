/**
 * Upstox Trading Automation — Terminal JS UI
 */

const API_BASE = window.location.origin;
const WS_URL = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws";

let ws = null;
let currentInstrumentKey = "NSE_INDEX|Nifty 50";
let currentInstrumentName = "Nifty 50";
let currentInterval = "15minute";
let chart = null;
let candleSeries = null;
let supertrendSeries = null;

// ── Initialization ──────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    updateClock();
    setInterval(updateClock, 1000);
    
    initChart();
    connectWebSocket();
    checkAuth();
    refreshStatus();
    
    // Default tab
    switchBottomTab('positions');
    
    // Instrument Search
    const searchInp = document.getElementById("instrument-search");
    let searchTimeout;
    searchInp.addEventListener("input", (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length < 2) {
            // Revert back if empty
            if (query.length === 0) restoreDefaultWatchlist();
            return;
        }
        searchTimeout = setTimeout(() => fetchInstrumentSearch(query), 400);
    });
    
    // Load top instruments on boot
    restoreDefaultWatchlist();
    
    // Fetch generic python strategy parameters dynamically
    fetchStrategySchemas();
});

function setChartTimeframe(interval) {
    currentInterval = interval;
    showToast(`Loading ${interval} timeframe...`);
    
    
    if (supertrendSeries) {
        // clear old overlay before strategy loads
        supertrendSeries.setData([]);
        if (candleSeries) {
            candleSeries.setMarkers([]);
            candleSeries.setData([]); // Crucial: clear old candles to prevent marker sync crash
        }
    }
    
    // Automatically apply the strategy for the new timeframe
    // Fetch candles FIRST, then firmly await overlay binding
    fetchHistoricalCandles(currentInstrumentKey).then(() => {
        const selector = document.getElementById("strategy-selector");
        if (selector) {
            const cls = selector.options[selector.selectedIndex].dataset.class;
            fetchStrategyOverlay(currentInstrumentKey, interval, cls, getDynamicParams());
        }
    });
}

async function restoreDefaultWatchlist() {
    const list = document.getElementById("watchlist");
    list.innerHTML = `<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:0.8rem">Loading Nifty 50...</div>`;
    
    try {
        const res = await fetch(`${API_BASE}/market/instruments/featured`);
        const data = await res.json();
        
        if (data.instruments && data.instruments.length > 0) {
            list.innerHTML = "";
            data.instruments.forEach(inst => {
                const item = document.createElement("div");
                item.className = `watchlist-item ${currentInstrumentKey === inst.instrument_key ? 'active' : ''}`;
                item.onclick = () => selectInstrument(inst.instrument_key, inst.name);
                
                item.innerHTML = `
                    <div>
                        <div class="instrument-name">${inst.name}</div>
                        <div class="instrument-type">${inst.segment}</div>
                    </div>
                `;
                list.appendChild(item);
            });
        }
    } catch (e) {
        list.innerHTML = `<div style="padding:12px;text-align:center;color:var(--text-danger);font-size:0.8rem">Failed to load watchlist</div>`;
    }
}

async function fetchInstrumentSearch(query) {
    try {
        const res = await fetch(`${API_BASE}/market/instruments/search?query=${encodeURIComponent(query)}`);
        const data = await res.json();
        
        const list = document.getElementById("watchlist");
        list.innerHTML = "";
        
        if (data.instruments && data.instruments.length > 0) {
            data.instruments.forEach(inst => {
                const item = document.createElement("div");
                item.className = `watchlist-item ${currentInstrumentKey === inst.instrument_key ? 'active' : ''}`;
                item.onclick = (e) => selectInstrument(inst.instrument_key, inst.name, e.currentTarget);
                
                item.innerHTML = `
                    <div>
                        <div class="instrument-name">${inst.name}</div>
                        <div class="instrument-type">${inst.segment}</div>
                    </div>
                `;
                list.appendChild(item);
            });
        } else {
            list.innerHTML = `<div style="padding: 12px; font-size: 0.8rem; color: var(--text-muted); text-align: center;">No results</div>`;
        }
    } catch (e) {
        console.error("Search failed:", e);
    }
}

function updateClock() {
    const now = new Date();
    document.getElementById('clock').innerText = now.toLocaleTimeString('en-US', {
        hour12: true, hour: '2-digit', minute:'2-digit', second:'2-digit'
    }) + " IST";
}

// ── WebSockets ────────────────────────────────────────────────

function connectWebSocket() {
    const wsStatusText = document.getElementById("ws-status-text");
    const wsStatusBadge = document.getElementById("ws-status");
    
    ws = new WebSocket(WS_URL);
    
    ws.onopen = () => {
        wsStatusText.innerText = "Live";
        wsStatusBadge.className = "status-badge online";
        showToast("Connected to Engine WebSocket");
    };
    
    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error("WS Parse Error:", e);
        }
    };
    
    ws.onclose = () => {
        wsStatusText.innerText = "Reconnecting...";
        wsStatusBadge.className = "status-badge offline";
        setTimeout(connectWebSocket, 3000);
    };
    
    ws.onerror = (err) => {
        console.error("WS Error:", err);
        ws.close();
    };
}

function handleWsMessage(msg) {
    if (msg.type === "status") {
        updateUIWithStatus(msg.data);
    } else if (msg.type === "new_signal") {
        addLog(`🎯 New Signal: ${msg.data.action} on ${msg.data.instrument}`, "info");
        refreshSignals();
    } else if (msg.type === "trade_executed") {
        addLog(`💰 Trade Executed: ${msg.data.action} @ ${msg.data.price}`, "success");
        refreshTrades();
    } else if (msg.type === "market_data") {
        // If it's for our current instrument, update the chart
        if (msg.data.instrument_key === currentInstrumentKey && candleSeries) {
            candleSeries.update(msg.data.candle);
        }
    }
}

// ── Chart Management ──────────────────────────────────────────

function initChart() {
    const chartContainer = document.getElementById('tvchart');
    
    chart = LightweightCharts.createChart(chartContainer, {
        layout: {
            background: { type: 'solid', color: '#0a0a0f' },
            textColor: '#8b8b9e',
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
        },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
            timeVisible: true,
            secondsVisible: false,
        },
    });

    // Global right-click to reset view
    chartContainer.addEventListener('contextmenu', e => {
        e.preventDefault();
        chart.timeScale().fitContent();
        chart.priceScale('right').applyOptions({ autoScale: true });
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00d084',
        downColor: '#ff4757',
        borderVisible: false, // Added
        wickUpColor: '#00d084',
        wickDownColor: '#ff4757',
    });

    // Single unified LineSeries for SuperTrend with segment coloring
    supertrendSeries = chart.addLineSeries({
        lineWidth: 2,
        lineType: LightweightCharts.LineType.Step,
        crosshairMarkerVisible: false, // Added
    });
    
    // Resize observer
    new ResizeObserver(entries => {
        if (entries.length === 0 || entries[0].target !== chartContainer) { return; }
        const newRect = entries[0].contentRect;
        chart.applyOptions({ height: newRect.height, width: newRect.width });
    }).observe(chartContainer);

    // Initial load
    fetchHistoricalCandles(currentInstrumentKey);
}

// ── View Management & Option Chain ────────────────────────────

function switchMainView(viewType) {
    const btnChart = document.getElementById("btn-view-chart");
    const btnOptions = document.getElementById("btn-view-options");
    const btnSettings = document.getElementById("btn-view-settings-center");
    const tvchart = document.getElementById("tvchart");
    const ocGrid = document.getElementById("option-chain-container");
    const settingsView = document.getElementById("settings-view-container");

    // Reset Buttons
    [btnChart, btnOptions, btnSettings].forEach(b => { if(b) b.className = "btn btn-outline"; });
    
    // Hide all main containers
    [tvchart, ocGrid, settingsView].forEach(v => { if(v) v.style.display = "none"; });

    if (viewType === 'chart') {
        if(btnChart) btnChart.className = "btn btn-primary";
        tvchart.style.display = "block";
    } else if (viewType === 'options') {
        if(btnOptions) btnOptions.className = "btn btn-primary";
        ocGrid.style.display = "block";
        
        if (!currentInstrumentKey) {
            selectInstrument('NSE_INDEX|Nifty 50', 'Nifty 50');
        } else {
            fetchOptionChain(); 
        }
    } else if (viewType === 'settings') {
        if(btnSettings) btnSettings.className = "btn btn-primary";
        settingsView.style.display = "block";
        loadAllSettings();
    }
}

function switchSettingsTab(tabName) {
    document.querySelectorAll('.settings-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
    
    document.getElementById(`set-tab-${tabName}`).classList.add('active');
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('active');
    }
}

async function loadAllSettings() {
    try {
        const res = await fetch(`${API_BASE}/settings/`);
        const data = await res.json();
        document.getElementById('setting-api-key').value = data.API_KEY || '';
        document.getElementById('setting-api-secret').value = '********';
        document.getElementById('setting-redirect-uri').value = data.REDIRECT_URI || '';
        
        // Also load risk params & engine state
        fetchRiskConfig();
        renderDynamicStrategyForm();
    } catch (e) {
        console.error("Failed to load settings", e);
    }
}

async function fetchRiskConfig() {
    try {
        const res = await fetch(`${API_BASE}/engine/status`);
        const data = await res.json();
        
        // Update General Tab toggles
        const paperToggle = document.getElementById('toggle-papermode');
        if (paperToggle) paperToggle.checked = data.paper_trading;
        
        const sideSelect = document.getElementById('setting-trading-side');
        if (sideSelect) sideSelect.value = data.trading_side || 'BOTH';
        
        // Update Risk Tab inputs
        const risk = data.risk_controls || {};
        const capInput = document.getElementById('risk-capital');
        const pctInput = document.getElementById('risk-pct');
        const lossInput = document.getElementById('risk-maxloss');
        const tradesInput = document.getElementById('risk-maxtrades');
        
        if (capInput) capInput.value = risk.trading_capital || 100000;
        if (pctInput) pctInput.value = risk.risk_per_trade_pct || 1.0;
        if (lossInput) lossInput.value = risk.max_daily_loss_pct || 3.0;
        if (tradesInput) tradesInput.value = risk.max_open_trades || 3;
        
    } catch (e) {
        console.error("Failed to fetch risk config", e);
    }
}

async function saveRiskConfig() {
    const payload = {
        trading_capital: parseFloat(document.getElementById('risk-capital').value),
        risk_per_trade_pct: parseFloat(document.getElementById('risk-pct').value),
        max_daily_loss_pct: parseFloat(document.getElementById('risk-maxloss').value),
        max_open_trades: parseInt(document.getElementById('risk-maxtrades').value)
    };
    
    try {
        const res = await fetch(`${API_BASE}/engine/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast("Risk parameters updated", "success");
        }
    } catch (e) {
        showToast("Failed to save risk config", "error");
    }
}

async function togglePaperMode(isPaper) {
    try {
        await fetch(`${API_BASE}/engine/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paper_trading: isPaper })
        });
        showToast(`Paper Trading: ${isPaper ? 'Enabled' : 'Disabled'}`, "info");
    } catch (e) { console.error(e); }
}

async function updateTradingSide(side) {
    try {
        await fetch(`${API_BASE}/engine/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ trading_side: side })
        });
        showToast(`Execution: ${side}`, "info");
    } catch (e) { console.error(e); }
}

async function triggerTestSignal() {
    if (!currentInstrumentKey) {
        showToast("Select a stock first", "warning");
        return;
    }
    try {
        showToast("Triggering test signal...", "info");
        const res = await fetch(`${API_BASE}/engine/test-signal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instrument_key: currentInstrumentKey })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast("Signal fired! Checking outcome...", "success");
        } else {
            showToast(data.message || "Signal failed", "error");
        }
    } catch (e) { showToast("API Error", "error"); }
}

async function fetchOptionChain(forcedExpiry = "") {
    if (!currentInstrumentKey) return;
    
    const select = document.getElementById("oc-expiry-select");
    const expiry = forcedExpiry || select.value;
    
    document.getElementById("oc-instrument-name").innerText = currentInstrumentName;
    const tbody = document.getElementById("oc-tbody");
    tbody.innerHTML = `<tr><td colspan="11" style="padding: 40px; text-align: center; color: var(--text-muted);">Fetching live Option Chain [${expiry || 'Nearest'}]...</td></tr>`;

    try {
        const url = `${API_BASE}/market/option-chain?instrument_key=${encodeURIComponent(currentInstrumentKey)}${expiry ? '&expiry_date='+encodeURIComponent(expiry) : ''}`;
        const res = await fetch(url);
        const data = await res.json();
        
        if (data.status === "success" && data.chain) {
            // Populate Expiry drop-down if available
            if (data.available_expiries && (select.dataset.instrument !== currentInstrumentKey || select.options.length <= 1)) {
                 select.innerHTML = data.available_expiries.map(exp => 
                    `<option value="${exp}" ${exp === data.expiry_date ? 'selected' : ''}>${exp}</option>`
                 ).join("");
                 select.dataset.instrument = currentInstrumentKey;
            }
            
            tbody.innerHTML = "";
            
            // Find ATM strike (closest to spot_price)
            let closestDiff = Infinity;
            let atmIndex = -1;
            data.chain.forEach((row, idx) => {
                const diff = Math.abs(row.strike_price - data.spot_price);
                if (diff < closestDiff) {
                    closestDiff = diff;
                    atmIndex = idx;
                }
            });

            data.chain.forEach((row, idx) => {
                const ce = row.ce || {};
                const pe = row.pe || {};
                const isATM = idx === atmIndex;
                
                // ITM Shading: Calls are ITM if strike < spot. Puts are ITM if strike > spot.
                const ceITM = row.strike_price < data.spot_price;
                const peITM = row.strike_price > data.spot_price;
                
                const tr = document.createElement("tr");
                tr.style.borderBottom = "1px solid var(--border-color)";
                if (isATM) {
                    tr.style.border = "1px solid #00d084";
                    tr.id = "atm-row";
                }
                
                tr.innerHTML = `
                    <td style="color:var(--text-muted); font-size:0.7rem; padding: 4px;">${(ce.delta || 0).toFixed(2)}</td>
                    <td style="color:var(--text-muted); font-size:0.7rem; padding: 4px;">${(ce.theta || 0).toFixed(2)}</td>
                    <td style="padding: 4px; color:var(--text-muted);">${(ce.iv || 0).toFixed(1)}%</td>
                    <td style="padding: 4px; width: 60px;">
                        <div style="font-size:0.65rem; color: #8b8b9e;">${(ce.volume || 0).toLocaleString()}</div>
                        <div style="height:2px; background:#00d084; width:${Math.min(100, (ce.volume || 0)/1000)}%; opacity:0.5;"></div>
                    </td>
                    <td style="font-weight:bold;color:#10b981; background:${ceITM ? 'rgba(16,185,129,0.08)' : 'transparent'}; padding: 4px;">${(ce.ltp || 0).toFixed(2)}</td>
                    
                    <td style="font-weight:bold; background:var(--bg-dark); color:var(--text-color); padding: 4px; border-left:1px solid var(--border-color); border-right:1px solid var(--border-color);">
                        ${row.strike_price.toFixed(0)}
                    </td>
                    
                    <td style="font-weight:bold;color:#ef4444; background:${peITM ? 'rgba(239,68,68,0.08)' : 'transparent'}; padding: 4px;">${(pe.ltp || 0).toFixed(2)}</td>
                    <td style="padding: 4px; width: 60px;">
                        <div style="font-size:0.65rem; color: #8b8b9e;">${(pe.volume || 0).toLocaleString()}</div>
                        <div style="height:2px; background:#ef4444; width:${Math.min(100, (pe.volume || 0)/1000)}%; opacity:0.5;"></div>
                    </td>
                    <td style="padding: 4px; color:var(--text-muted);">${(pe.iv || 0).toFixed(1)}%</td>
                    <td style="color:var(--text-muted); font-size:0.7rem; padding: 4px;">${(pe.theta || 0).toFixed(2)}</td>
                    <td style="color:var(--text-muted); font-size:0.7rem; padding: 4px;">${(pe.delta || 0).toFixed(2)}</td>
                `;
                tbody.appendChild(tr);
            });

            // Auto-scroll to ATM row
            setTimeout(() => {
                const atm = document.getElementById("atm-row");
                if (atm) atm.scrollIntoView({ block: 'center', behavior: 'smooth' });
            }, 100);

            
            if (data.chain.length === 0) {
                 tbody.innerHTML = `<tr><td colspan="11" style="padding: 40px; text-align: center; color: var(--text-muted);">No option contracts found. Check if the market is open or if the asset has derivatives.</td></tr>`;
            }
        } else {
             tbody.innerHTML = `<tr><td colspan="11" style="padding: 40px; text-align: center; color: var(--text-danger);">${data.message || 'Failed to fetch chain'}</td></tr>`;
        }
    } catch(e) {
        tbody.innerHTML = `<tr><td colspan="11" style="padding: 40px; text-align: center; color: var(--text-danger);">Network error fetching Options Chain</td></tr>`;
    }
}


async function selectInstrument(instrumentKey, name) {
    if (currentInstrumentKey === instrumentKey) return;
    
    currentInstrumentKey = instrumentKey;
    currentInstrumentName = name;
    
    // Update active state in UI
    document.querySelectorAll('.watchlist-item').forEach(item => {
        // We find the item by checking if it contains the name or matches some data attribute if we had one
        // Better: check the name or re-render if needed, but for now let's use name match or just iterate
        const itemHover = item.querySelector('.instrument-name');
        if (itemHover && itemHover.innerText === name) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    // Update UI Labels
    const label = document.getElementById("current-instrument");
    if (label) {
        label.innerHTML = `${name} <span class="badge" style="background: rgba(0, 208, 132, 0.1); color: #00d084; font-size: 0.65rem;">Live</span>`;
    }
    
    // Automatically perform sequential load of candles and strategy matrix
    if (document.getElementById("tvchart").style.display !== "none") {
        setChartTimeframe(currentInterval);
    } else {
        fetchOptionChain();
    }
}

function toggleSidebar() {
    const sidebar = document.querySelector('.panel-left');
    const mainContent = document.querySelector('.panel-center'); // Matching existing layout class
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
        if (mainContent) mainContent.classList.toggle('expanded');
    }
}

async function fetchHistoricalCandles(instrumentKey) {
    try {
        const toDateObj = new Date();
        const fromDateObj = new Date();
        
        // Dynamically cap historical data fetching to prevent Upstox API crashes
        // Upstox caps 1-min interval (used for 1m-1H) to ~30 days max. We use 28 to safely account for timezone wrapping.
        const isMinuteScale = currentInterval.includes('m') || currentInterval.includes('H') || currentInterval.includes('minute') || currentInterval === '1hour';
        const daysBack = isMinuteScale ? 28 : 365;
        
        fromDateObj.setDate(toDateObj.getDate() - daysBack);
        
        // Buffer toDate by +1 day (86400000ms) to ensure we always capture the absolute latest intraday bars
        const toDateStr = new Date(Date.now() + 86400000).toISOString().split('T')[0];
        const fromDateStr = fromDateObj.toISOString().split('T')[0];
        
        const res = await fetch(`${API_BASE}/market/candles?instrument_key=${encodeURIComponent(instrumentKey)}&interval=${currentInterval}&from_date=${fromDateStr}&to_date=${toDateStr}`);
        if (res.ok) {
            const data = await res.json();
            if (data.candles && data.candles.length > 0) {
                const formatted = data.candles.map(c => {
                    // Convert ISO string to unix timestamp in seconds, then shift by +19800 (5.5h) for IST visualization
                    const ds = (new Date(c.datetime).getTime() / 1000) + 19800;
                    return {
                        time: ds,
                        open: c.open,
                        high: c.high,
                        low: c.low,
                        close: c.close
                    };
                });
                
                // Sort ascending by time (oldest to newest)
                formatted.sort((a,b) => a.time - b.time);
                
                candleSeries.setData(formatted);
                
                // Force auto-scale on instrument change so the view doesn't get stuck at the previous instrument's price range
                chart.priceScale('right').applyOptions({ autoScale: true });
                chart.timeScale().fitContent();
                
                return;
            }
        }
    } catch (e) {
        console.warn("Failed to fetch real historical data, using dummy data");
    }
    
// Fallback sequence removed to prevent corrupted UI state. Any valid historical array cleanly populates the viewer.
}

// ── UI Interactions ───────────────────────────────────────────

function switchBottomTab(tabId) {
    document.querySelectorAll('.bottom-tab').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.bottom-content').forEach(el => el.classList.remove('active'));
    
    const btn = document.querySelector(`.bottom-tab[onclick="switchBottomTab('${tabId}')"]`);
    if (btn) btn.classList.add('active');
    
    document.getElementById(`tab-${tabId}`).classList.add('active');
    
    if (tabId === 'trades') refreshTrades();
    if (tabId === 'signals') refreshSignals();
}

function addLog(msg, type="info") {
    const logViewer = document.getElementById("activity-log");
    const div = document.createElement("div");
    div.className = "log-line";
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    div.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-msg ${type}">${msg}</span>`;
    logViewer.appendChild(div);
    
    // Auto scroll bottom
    const pnl = document.getElementById('tab-activity');
    pnl.scrollTop = pnl.scrollHeight;
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = "fadeOut 0.3s ease forwards";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ── API Calls ─────────────────────────────────────────────────

async function checkAuth() {
    try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await res.json();
        const badge = document.getElementById("auth-status");
        const text = document.getElementById("auth-status-text");
        
        if (data.auth === "valid") {
            badge.className = "status-badge online";
            text.innerText = "Auth Valid";
        } else {
            badge.className = "status-badge offline";
            text.innerText = "Needs Login";
        }
    } catch (e) {
        document.getElementById("auth-status-text").innerText = "API Offline";
    }
}

async function loginUpstox() {
    try {
        const res = await fetch(`${API_BASE}/auth/login`);
        const data = await res.json();
        if (data.auth_url) {
            window.location.href = data.auth_url;
        }
    } catch (e) {
        showToast("Backend not reachable", "error");
    }
}

async function initializeEngine() {
    addLog("Initializing Engine...");
    try {
        const res = await fetch(`${API_BASE}/engine/initialize`, { method: "POST" });
        const data = await res.json();
        if (data.status === "initialized") {
            showToast("Engine Initialized", "success");
            addLog("Engine booted successfully.", "success");
            refreshStatus();
        } else {
            showToast("Init failed. Check terminal.", "error");
            addLog("Engine failed to initialize.", "error");
        }
    } catch (e) {
        showToast("Error initializing", "error");
    }
}

async function runCycle() {
    addLog("Triggering manual eval cycle...");
    try {
        const res = await fetch(`${API_BASE}/engine/run-cycle`, { method: "POST" });
        const data = await res.json();
        showToast("Cycle complete", "success");
        addLog("Manual cycle completed successfully.", "success");
        refreshSignals();
        refreshTrades();
    } catch (e) {
        showToast("Error running cycle", "error");
    }
}

async function toggleAutoMode(enabled) {
    try {
        const res = await fetch(`${API_BASE}/engine/auto-mode?enabled=${enabled}`, { method: "POST" });
        const data = await res.json();
        
        const card = document.getElementById("auto-mode-card");
        if (data.auto_mode) {
            card.classList.add("active");
            showToast("Auto-Mode ENABLED", "success");
            addLog("🤖 Auto-Mode engaged. Bot will trade autonomously.", "success");
        } else {
            card.classList.remove("active");
            showToast("Auto-Mode DISABLED", "info");
            addLog("🛑 Auto-Mode disengaged.", "warning");
        }
    } catch (e) {
        showToast("Failed to toggle auto mode", "error");
        // Revert toggle
        document.getElementById("toggle-automode").checked = !enabled;
    }
}

async function saveRiskConfig() {
    const capital = parseFloat(document.getElementById("risk-capital").value);
    const riskPct = parseFloat(document.getElementById("risk-pct").value);
    const maxLoss = parseFloat(document.getElementById("risk-maxloss").value);
    const maxTrades = parseInt(document.getElementById("risk-maxtrades").value);
    
    try {
        const params = new URLSearchParams({
            trading_capital: capital,
            risk_per_trade_pct: riskPct,
            max_daily_loss_pct: maxLoss,
            max_open_trades: maxTrades
        });
        const res = await fetch(`${API_BASE}/engine/config?${params.toString()}`, { method: "POST" });
        if (res.ok) {
            showToast("Risk Config Saved", "success");
            addLog(`Risk updated: ${riskPct}% risk, ${maxLoss}% max loss limit.`, "info");
            refreshStatus();
        }
    } catch (e) {
        showToast("Failed to save config", "error");
    }
}

let dynamicSchemas = {};

async function fetchStrategySchemas() {
    try {
        const res = await fetch(`${API_BASE}/strategies/schema`);
        if (!res.ok) return;
        const data = await res.json();
        
        const selector = document.getElementById("strategy-selector");
        selector.innerHTML = "";
        
        data.strategies.forEach(s => {
            dynamicSchemas[s.id] = s;
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.dataset.class = s.class;
            opt.innerText = s.name;
            selector.appendChild(opt);
        });
        
        renderDynamicStrategyForm();
    } catch(e) {
        console.error("Failed to load strategy schemas", e);
    }
}

function renderDynamicStrategyForm() {
    const selector = document.getElementById("strategy-selector");
    if (!selector) return;
    const sid = selector.value;
    const schema = dynamicSchemas[sid];
    if (!schema) return;
    
    const container = document.getElementById("dynamic-strategy-container");
    container.innerHTML = "";
    
    schema.params.forEach(p => {
        if (p.type === 'boolean') {
            container.innerHTML += `
                <div class="form-group" style="margin-top: 8px;">
                    <label style="display: flex; align-items: center; gap: 8px; font-size: 0.8rem; color: var(--text-secondary);">
                        <input type="checkbox" class="dyn-param" data-name="${p.name}" ${p.default ? 'checked' : ''}> ${p.name.replace(/_/g, ' ')}
                    </label>
                </div>`;
        } else if (p.type === 'number') {
            container.innerHTML += `
                <div class="form-group" style="margin-top: 8px;">
                    <label class="form-label">${p.name.replace(/_/g, ' ')}</label>
                    <input type="number" class="form-input dyn-param" data-name="${p.name}" value="${p.default}" ${p.name.includes('mult') || p.name.includes('pct') ? 'step="0.1"' : ''}>
                </div>`;
        } else {
            container.innerHTML += `
                <div class="form-group" style="margin-top: 8px;">
                    <label class="form-label">${p.name.replace(/_/g, ' ')}</label>
                    <input type="text" class="form-input dyn-param" data-name="${p.name}" value="${p.default}">
                </div>`;
        }
    });
}

function getDynamicParams() {
    const params = {};
    document.querySelectorAll('.dyn-param').forEach(el => {
        let val;
        if (el.type === 'checkbox') val = el.checked;
        else if (el.type === 'number') val = Number(el.value);
        else val = el.value;
        params[el.dataset.name] = val;
    });
    return params;
}

async function loadStrategy() {
    if (!currentInstrumentKey) {
        showToast("Please select an instrument first", "warning");
        return;
    }
    
    const selector = document.getElementById("strategy-selector");
    const strategyId = selector.value;
    const strategyClass = selector.options[selector.selectedIndex].dataset.class;
    const strategyName = selector.options[selector.selectedIndex].innerText;
    
    try {
        const payloadParams = getDynamicParams();
        
        const params = new URLSearchParams({
            strategy_class: strategyClass,
            name: strategyName,
            instruments: currentInstrumentKey,
            timeframe: currentInterval,
            paper_trading: true // Fixed to paper trading for safety right now
        });
        
        const res = await fetch(`${API_BASE}/engine/load-strategy?${params.toString()}`, { 
            method: "POST",
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payloadParams)
        });
        if (res.ok) {
            showToast(`Applied strategy to ${currentInstrumentName}`, "success");
            addLog(`Loaded SuperTrend Pro on ${currentInstrumentName}`, "success");
            refreshStatus();
            
            // Now fetch the visual overlay to plot on the chart!
            const dynParams = getDynamicParams();
            fetchStrategyOverlay(currentInstrumentKey, currentInterval, strategyClass, dynParams);
        }
    } catch (e) {
        showToast("Failed to apply strategy", "error");
    }
}

// Helper to convert ISO string to unix timestamp in seconds, shifted for IST
function parseUpstoxDate(isoString) {
    if (!isoString) return null;
    return (new Date(isoString).getTime() / 1000) + 19800;
}

async function fetchStrategyOverlay(instrumentKey, timeframe, strategyClass, customParams) {
    try {
        const toDateObj = new Date();
        const fromDateObj = new Date();
        
        // Sync the overlay math boundary to match the dynamic candlestick query
        const isMinuteScale = timeframe.includes('m') || timeframe.includes('H') || timeframe.includes('minute') || timeframe === '1hour';
        const daysBack = isMinuteScale ? 28 : 365;
        
        fromDateObj.setDate(toDateObj.getDate() - daysBack);
        
        const toDateStr = toDateObj.toISOString().split('T')[0];
        const fromDateStr = fromDateObj.toISOString().split('T')[0];

        const params = new URLSearchParams({
            instrument_key: instrumentKey,
            timeframe: timeframe,
            from_date: fromDateStr,
            to_date: toDateStr,
            strategy_class: strategyClass,
            params: JSON.stringify(customParams)
        });
        const res = await fetch(`${API_BASE}/market/strategy-overlay?${params.toString()}`);
        if (res.ok) {
            const data = await res.json();
            if (data.status === "success" && data.overlay) {
                
                // Natively render HUD matrix from overlay payload independently of engine status
                if (data.latest_metrics) {
                    renderStrategyHUD({ latest_metrics: data.latest_metrics });
                }
                
                const stData = []; // Changed from upperData/lowerData
                const markers = [];
                let lastTrend = null;
                
                data.overlay.forEach(pt => {
                    const ds = parseUpstoxDate(pt.datetime);
                    if (!ds) return;
                    
                    if (pt.supertrend !== null) {
                        // Inject unique color line segment per point based on trend
                        const segColor = pt.trend === 1 ? '#00d084' : '#ff4757';
                        stData.push({ time: ds, value: pt.supertrend, color: segColor });
                    }
                    // Removed the logic to break lines when trend changes, as color handles segments
                    
                    if (lastTrend !== null && pt.trend !== lastTrend) {
                        if (pt.trend === 1) {
                            markers.push({
                                time: ds, position: 'belowBar', color: '#00d084', // Updated color
                                shape: 'arrowUp', text: 'BUY'
                            });
                        } else if (pt.trend === -1) {
                            markers.push({
                                time: ds, position: 'aboveBar', color: '#ff4757', // Updated color
                                shape: 'arrowDown', text: 'SELL'
                            });
                        }
                    }
                    lastTrend = pt.trend;
                });
                
                stData.sort((a,b) => a.time - b.time); // Sorted single data array
                
                if (supertrendSeries) supertrendSeries.setData(stData); // Updated to single series
                if (candleSeries) candleSeries.setMarkers(markers);
                
                showToast("Indicator Plot Updated", "info");
            }
        }
    } catch (e) {
        console.error("Failed to fetch overlay", e);
    }
}

// ── Status Pollers ────────────────────────────────────────────

async function refreshStatus() {
    try {
        const res = await fetch(`${API_BASE}/engine/status`);
        const data = await res.json();
        updateUIWithStatus(data);
    } catch (e) {}
}

function updateUIWithStatus(data) {
    const statusText = document.getElementById("engine-status-text");
    if (data.initialized) {
        statusText.innerText = data.running ? "Running" : "Idle / Valid";
        statusText.className = "text-success";
    } else {
        statusText.innerText = "Not Initialized";
        statusText.className = "text-warning";
    }
    
    // Auto Mode Toggle sync
    if (data.auto_mode !== undefined) {
        document.getElementById("toggle-automode").checked = data.auto_mode;
        if (data.auto_mode) {
            document.getElementById("auto-mode-card").classList.add("active");
        } else {
            document.getElementById("auto-mode-card").classList.remove("active");
        }
    }
    
    // Risk Display sync
    if (data.risk_controls) {
        const rc = data.risk_controls;
        document.getElementById("risk-capital").value = rc.trading_capital;
        document.getElementById("risk-pct").value = rc.risk_per_trade_pct;
        document.getElementById("risk-maxloss").value = rc.max_daily_loss_pct;
        document.getElementById("risk-maxtrades").value = rc.max_open_trades;
        
        const maxLossAbs = (rc.trading_capital * (rc.max_daily_loss_pct / 100)).toFixed(2);
        document.getElementById("disp-maxloss").innerText = `-₹${maxLossAbs}`;
    }
    
    // Daily PNL
    if (data.daily_pnl !== undefined) {
        const dpnl = data.daily_pnl;
        const color = dpnl >= 0 ? "text-success" : "text-danger";
        document.getElementById("disp-pnl").innerText = `${dpnl >= 0 ? '+' : '-'}₹${Math.abs(dpnl).toFixed(2)}`;
        document.getElementById("disp-pnl").className = `mono ${color}`;
    }
    
    // Strategy HUD sync
    if (data.active_strategies && data.active_strategies.length > 0) {
        renderStrategyHUD(data.active_strategies[0]);
    } else {
        document.getElementById("strategy-hud-container").innerHTML = `
            <div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 0.8rem;">
                Waiting for active strategy...
            </div>
        `;
    }
}

function renderStrategyHUD(strategy) {
    const m = strategy.latest_metrics;
    if (!m) return;
    
    // Helper to format rows identical to TradingView
    const bgHdr = "background: #1e222d; color: white;";
    const bgRow = "background: #2a2e39; color: white;";
    const bgCyn = "background: rgba(0, 188, 212, 0.2); color: #00bcd4;";
    const bgOrn = "background: rgba(255, 152, 0, 0.2); color: #ff9800;";
    
    const cGrn = "background: rgba(76, 175, 80, 0.2); color: #4caf50;";
    const cRed = "background: rgba(244, 67, 54, 0.2); color: #ff5252;";
    const cGry = "background: rgba(158, 158, 158, 0.2); color: #9e9e9e;";
    const cYlw = "background: rgba(255, 235, 59, 0.2); color: #ffeb3b;";
    
    const passCol = (ok) => ok ? cGrn : cRed;
    
    let html = `<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #404040; font-family: monospace; font-size: 0.75rem;">`;
    
    const addRow = (label, val, bgLabel, bgVal) => {
        html += `<div style="padding: 4px 8px; ${bgLabel}">${label}</div>`;
        html += `<div style="padding: 4px 8px; ${bgVal}">${val}</div>`;
    };
    
    addRow("Metric", "Value", bgHdr, bgHdr);
    addRow("TF profile", `${m.tf_profile} (${m.tf_mode})`, bgCyn, bgCyn);
    addRow("Exit mode", m.exit_mode, bgCyn, bgCyn);
    addRow("ST Trend", m.trend, bgRow, m.trend === "LONG" ? cGrn : cRed);
    
    addRow("H1 Dual ST", m.hard_gates.dual_st, bgOrn, m.hard_gates.dual_st === "AGREE" ? cGrn : cRed);
    let consecOk = parseInt(m.hard_gates.consecutive.split('/')[0]) >= parseInt(m.hard_gates.consecutive.split('/')[1]);
    addRow("H2 Consec", `${m.hard_gates.consecutive} ${consecOk ? 'PASS' : 'FAIL'}`, bgOrn, passCol(consecOk));
    
    addRow("Soft score", m.soft_filters.score, bgRow, cYlw); // could be green/yellow/red, simplifying for now
    
    addRow("S1 ADX", `${m.soft_filters.adx.value} ${m.soft_filters.adx.pass ? 'PASS' : 'FAIL'}`, bgRow, passCol(m.soft_filters.adx.pass));
    addRow("S2 Volume", m.soft_filters.volume.pass ? "SURGE" : "FLAT", bgRow, passCol(m.soft_filters.volume.pass));
    addRow("S3 ATR%", `${m.soft_filters.atr_pct.value}%`, bgRow, passCol(m.soft_filters.atr_pct.pass));
    addRow("S4 ROC", `${m.soft_filters.roc.value}%`, bgRow, passCol(m.soft_filters.roc.pass));
    
    let bb = m.soft_filters.bb_squeeze;
    addRow("S5 BB", bb.state, bgRow, bb.pass ? cGrn : (bb.state === 'SQUEEZE' ? cYlw : cRed));
    
    addRow("Bars held", m.bars_in_trend, bgRow, bgRow);
    
    html += `</div>`;
    document.getElementById("strategy-hud-container").innerHTML = html;
}

async function refreshTrades() {
    try {
        const res = await fetch(`${API_BASE}/engine/trades`);
        const data = await res.json();
        const tbody = document.getElementById('trades-body');
        
        if (!data.trades || data.trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 20px;" class="text-muted">No trades today</td></tr>`;
            return;
        }
        
        tbody.innerHTML = data.trades.reverse().map(t => {
            const time = new Date(t.timestamp).toLocaleTimeString();
            let actionBadge = `<span class="badge ${t.action.toLowerCase() === 'buy' ? 'buy' : 'sell'}">${t.action}</span>`;
            let typeBadge = `<span class="badge ${t.type === 'paper' ? 'paper' : 'live'}">${t.type}</span>`;
            
            return `<tr>
                <td class="text-muted"><small>${time}</small></td>
                <td>${actionBadge}</td>
                <td>${t.instrument}</td>
                <td>₹${t.price}</td>
                <td class="text-danger">₹${t.stop_loss || '-'}</td>
                <td class="text-success">₹${t.take_profit || '-'}</td>
                <td>${typeBadge}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error("Trades refresh failed", e);
    }
}

async function refreshSignals() {
    try {
        const res = await fetch(`${API_BASE}/engine/signals`);
        const data = await res.json();
        const tbody = document.getElementById('signals-body');
        
        if (!data.signals || data.signals.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding: 20px;" class="text-muted">No signals generated</td></tr>`;
            return;
        }
        
        tbody.innerHTML = data.signals.reverse().map(s => {
            const time = new Date(s.timestamp).toLocaleTimeString();
            let actionBadge = `<span class="badge ${s.action.toLowerCase() === 'buy' ? 'buy' : 'sell'}">${s.action}</span>`;
            
            return `<tr>
                <td class="text-muted"><small>${time}</small></td>
                <td>${actionBadge}</td>
                <td>${s.instrument}</td>
                <td>₹${s.price.toFixed(2)}</td>
                <td>${s.confidence}/5</td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error("Signals refresh failed", e);
    }
}

async function saveSettings() {
    const key = document.getElementById('setting-api-key').value;
    const secret = document.getElementById('setting-api-secret').value;
    const uri = document.getElementById('setting-redirect-uri').value;
    
    const payload = {};
    if (key && !key.includes('...')) payload.API_KEY = key;
    if (secret && !secret.includes('***')) payload.API_SECRET = secret;
    if (uri) payload.REDIRECT_URI = uri;
    
    try {
        const res = await fetch(`${API_BASE}/settings/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast("Settings saved to .env securely", "success");
        } else {
            showToast("Failed to save settings", "error");
        }
    } catch (e) {
        showToast("Error saving settings", "error");
    }
}

async function renderDynamicStrategyForm() {
    const selector = document.getElementById('strategy-selector');
    if (!selector) return;
    const strategyId = selector.value;
    const container = document.getElementById('dynamic-strategy-container');
    if (!container) return;
    
    try {
        const res = await fetch(`${API_BASE}/strategies/schema`);
        const data = await res.json();
        const schema = data.strategies.find(s => s.id === strategyId);
        
        if (!schema) return;
        
        container.innerHTML = schema.params.map(p => {
            let input = '';
            if (p.type === 'boolean') {
                input = `<div style="display:flex; align-items:center; height:100%;"><input type="checkbox" id="param-${p.name}" ${p.default ? 'checked' : ''}></div>`;
            } else if (p.type === 'number') {
                input = `<input type="number" id="param-${p.name}" value="${p.default}" class="form-input" step="any">`;
            } else {
                input = `<input type="text" id="param-${p.name}" value="${p.default}" class="form-input">`;
            }
            
            return `
                <div class="form-group">
                    <label class="form-label">${p.name.replace(/_/g, ' ').toUpperCase()}</label>
                    ${input}
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error("Failed to load strategy schema", e);
    }
}

async function loadStrategy() {
    const selector = document.getElementById('strategy-selector');
    if (!selector) return;
    
    const strategyId = selector.value;
    const strategyClass = selector.options[selector.selectedIndex].dataset.class;
    
    // Collect params from dynamic inputs
    const params = {};
    const inputs = document.querySelectorAll('#dynamic-strategy-container input');
    inputs.forEach(input => {
        const name = input.id.replace('param-', '');
        if (input.type === 'checkbox') {
            params[name] = input.checked;
        } else if (input.type === 'number') {
            params[name] = parseFloat(input.value);
        } else {
            params[name] = input.value;
        }
    });

    try {
        // 1. Update params in registry
        await fetch(`${API_BASE}/strategies/${strategyId}/params`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });

        // 2. Load strategy into engine
        const paperMode = document.getElementById('toggle-papermode').checked;
        const res = await fetch(`${API_BASE}/engine/load-strategy?strategy_class=${strategyClass}&name=${encodeURIComponent(selector.options[selector.selectedIndex].text)}&instruments=${encodeURIComponent(currentInstrumentKey)}&paper_trading=${paperMode}`, {
            method: 'POST'
        });
        
        const result = await res.json();
        if (result.status === 'loaded') {
            showToast(`Strategy ${result.strategy} applied successfully`, "success");
            switchMainView('chart');
        }
    } catch (e) {
        showToast("Failed to apply strategy settings", "error");
    }
}
