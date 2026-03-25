/**
 * Upstox Trading Automation — Terminal JS UI
 */

const API_BASE = "http://localhost:8210";
const WS_URL = "ws://localhost:8210/ws";

let ws = null;
let currentInstrumentKey = "NSE_INDEX|Nifty 50";
let currentInstrumentName = "Nifty 50";
let chart = null;
let candleSeries = null;
let supertrendUpper = null;
let supertrendLower = null;

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
});

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

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00d084',
        downColor: '#ff4757',
        borderDownColor: '#ff4757',
        borderUpColor: '#00d084',
        wickDownColor: '#ff4757',
        wickUpColor: '#00d084',
    });

    supertrendUpper = chart.addLineSeries({
        color: '#ff4757',
        lineWidth: 2,
    });
    
    supertrendLower = chart.addLineSeries({
        color: '#00d084',
        lineWidth: 2,
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

async function selectInstrument(instrumentKey, name) {
    document.querySelectorAll('.watchlist-item').forEach(el => el.classList.remove('active'));
    event.currentTarget.classList.add('active');
    
    currentInstrumentKey = instrumentKey;
    currentInstrumentName = name;
    
    showToast(`Loaded ${name}`);
    await fetchHistoricalCandles(instrumentKey);
}

async function fetchHistoricalCandles(instrumentKey) {
    try {
        const res = await fetch(`${API_BASE}/market/candles?instrument_key=${encodeURIComponent(instrumentKey)}&interval=15minute`);
        if (res.ok) {
            const data = await res.json();
            if (data.candles && data.candles.length > 0) {
                const formatted = data.candles.map(c => {
                    // Convert ISO string to unix timestamp in seconds
                    const ds = new Date(c.datetime).getTime() / 1000;
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
                return;
            }
        }
    } catch (e) {
        console.warn("Failed to fetch real historical data, using dummy data");
    }
    
    // Fallback Dummy Data for UI preview
    const dummyData = [];
    let time = Math.floor(Date.now() / 1000) - 86400; // 1 day ago
    let lastClose = 22000;
    
    for (let i = 0; i < 100; i++) {
        time += 900; // 15 mins
        const open = lastClose + (Math.random() - 0.5) * 50;
        const high = open + Math.random() * 50;
        const low = open - Math.random() * 50;
        const close = (open + high + low) / 3;
        lastClose = close;
        
        dummyData.push({ time, open, high, low, close });
    }
    
    candleSeries.setData(dummyData);
}

// ── UI Interactions ───────────────────────────────────────────

function switchBottomTab(tabId) {
    document.querySelectorAll('.bottom-tab').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.bottom-content').forEach(el => el.classList.remove('active'));
    
    event.currentTarget.classList.add('active');
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

async function loadStrategy() {
    try {
        const params = new URLSearchParams({
            strategy_class: "SuperTrendPro",
            name: "SuperTrend Pro v6.3",
            instruments: currentInstrumentKey,
            timeframe: document.getElementById("param-timeframe").value,
            paper_trading: true // Fixed to paper trading for safety right now
        });
        
        const res = await fetch(`${API_BASE}/engine/load-strategy?${params.toString()}`, { method: "POST" });
        if (res.ok) {
            showToast(`Applied strategy to ${currentInstrumentName}`, "success");
            addLog(`Loaded SuperTrend Pro on ${currentInstrumentName}`, "success");
            refreshStatus();
        }
    } catch (e) {
        showToast("Failed to apply strategy", "error");
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
