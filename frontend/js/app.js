/**
 * app.js — Main application logic (ES Module).
 */

import { api } from './api.js';
import { showToast, switchTab, updateElementText, formatPrice } from './ui.js';
import { EngineWS } from './ws.js';
import { ChartManager } from './chart.js';

// Application State
let currentInstrumentKey = localStorage.getItem("currentInstrumentKey") || "NSE_INDEX|Nifty 50";
let currentInstrumentName = localStorage.getItem("currentInstrumentName") || "Nifty 50";
let currentInterval = localStorage.getItem("currentInterval") || "15minute";
let engineActive = false;
let dynamicSchemas = {};
const IST_OFFSET = 19800; // 5.5 hours for IST display

let globalSearchResults = [];
let selectedSearchIndex = -1;

// Services
const chart = new ChartManager('tvchart');
const ws = new EngineWS(handleWsMessage);

document.addEventListener("DOMContentLoaded", async () => {
    console.log("Terminal: Initializing...");
    
    // Initial data fetch - wrap in individual try/catch to avoid blocking others
    const safeLoad = async (fn) => { 
        try { 
            console.log(`Init: ${fn.name || 'anonymous'} started...`);
            await fn(); 
        } catch(e) { 
            console.error(`Init step failed: ${fn.name || 'anonymous'}`, e); 
        } 
    };
    
    try { chart.init(); } catch(e) { console.error("Chart Init Failed", e); }
    try { ws.connect(); } catch(e) { console.error("WS Connect Failed", e); }

    // Init UI Elements with persisted state
    updateElementText('current-instrument', currentInstrumentName);
    updateElementText('oc-instrument-name', currentInstrumentName);
    const activeBtn = document.getElementById(`tf-${currentInterval}`);
    if (activeBtn) {
        activeBtn.classList.remove('btn-outline');
        activeBtn.classList.add('btn-primary');
    }

    await safeLoad(fetchHistoricalCandles);
    await safeLoad(refreshAccountSummary);
    await safeLoad(refreshPositions);
    await safeLoad(refreshOrderBook);
    await safeLoad(refreshTrades);
    await safeLoad(refreshSignals);
    await safeLoad(checkAuth);
    await safeLoad(refreshStatus);
    await safeLoad(fetchStrategySchemas);
    await safeLoad(loadSettings);
    await safeLoad(refreshMarketStatus);
    
    // Set up listeners (ALWAYS run these)
    try {
        setupEventListeners();
        setupGlobalSearch();
        console.log("Terminal: Setup complete.");
    } catch(e) {
        console.error("Setup Listeners Failed", e);
    }
    
    // Interval updates
    setInterval(updateClock, 1000);
    setInterval(refreshAccountSummary, 60000);
    setInterval(refreshPositions, 30000);
    setInterval(refreshMarketStatus, 60000);
});

function setupEventListeners() {
    // Buy/Sell buttons
    document.getElementById('btn-buy')?.addEventListener('click', () => placeManualOrder('BUY'));
    document.getElementById('btn-sell')?.addEventListener('click', () => placeManualOrder('SELL'));
    
    // Instrument search
    const searchInp = document.getElementById("instrument-search");
    let searchTimeout;
    searchInp?.addEventListener("input", (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length === 0) {
            refreshWatchlist();
            return;
        }
        if (query.length < 2) return;
        searchTimeout = setTimeout(() => searchInstruments(query), 400);
    });

    // Strategy selector change
    document.getElementById("strategy-selector")?.addEventListener("change", () => {
        renderDynamicStrategyForm();
        refreshOverlay();
    });
}

function handleWsMessage(msg) {
    if (!msg || !msg.type) return;
    
    switch (msg.type) {
        case 'tick':
            chart.updateTick(msg.data);
            break;
        case 'signal':
            logActivity(`🎯 SIGNAL: ${msg.data.action} ${msg.data.instrument_key} @ ${msg.data.price}`, "signal");
            showToast(`New Signal: ${msg.data.action} on ${msg.data.trading_symbol || msg.data.instrument_key}`);
            refreshSignals();
            break;
        case 'trade':
            logActivity(`📦 TRADE: ${msg.data.action} ${msg.data.instrument_key} - ${msg.data.status}`, "trade");
            showToast(`Trade: ${msg.data.action} ${msg.data.status}`, msg.data.status === 'COMPLETE' ? 'success' : 'info');
            refreshTrades();
            refreshPositions();
            break;
        case 'error':
            logActivity(`❌ ERROR: ${msg.data.message}`, "error");
            showToast(msg.data.message, "error");
            break;
        case 'market_data':
            if (msg.data && msg.data.instrument_key === currentInstrumentKey) {
                const c = msg.data.candle;
                if (c && c.time && c.open != null && c.high != null && c.low != null && c.close != null) {
                    chart.updateCandle({ ...c, time: c.time + (IST_OFFSET || 0) });
                }
            }
            break;
        case 'portfolio_update':
            refreshPositions();
            refreshAccountSummary();
            break;
    }
}

async function fetchHistoricalCandles() {
    try {
        const data = await api.getHistoricalCandles(currentInstrumentKey, currentInterval);
        // Backend returns {instrument_key, count, candles}
        if (data && data.candles) {
            // Filter invalid candles and sort chronologically
            const valid = data.candles
                .filter(c => c && c.time && c.open != null && c.high != null && c.low != null && c.close != null)
                .sort((a, b) => a.time - b.time);
            
            // Deduplicate (LightweightCharts requires unique time)
            const unique = [];
            let lastT = null;
            for (const c of valid) {
                if (c.time !== lastT) {
                    unique.push(c);
                    lastT = c.time;
                }
            }
            
            chart.setData(unique.map(c => ({ ...c, time: c.time + IST_OFFSET })));
            if (unique.length === 0) {
                showToast("No candle data found for this interval", "warning");
            }
        }
    } catch (e) {
        console.error("Failed to fetch candles", e);
    }
}




async function selectInstrument(key, name) {
    currentInstrumentKey = key;
    currentInstrumentName = name;
    localStorage.setItem("currentInstrumentKey", key);
    localStorage.setItem("currentInstrumentName", name);
    
    updateElementText('current-instrument', name);
    updateElementText('oc-instrument-name', name); // Sync option chain header
    
    chart.clear();
    await fetchHistoricalCandles();
    await refreshOverlay();
}

async function placeManualOrder(side) {
    // Prevent index trading
    if (currentInstrumentKey.includes("NSE_INDEX") || currentInstrumentKey.includes("BSE_INDEX")) {
        showToast("Indices are not tradeable directly. Please select an option or future.", "warning");
        return;
    }

    const qty = prompt(`Enter Quantity for ${side} ${currentInstrumentName} (Check lot size first!):`, "1");
    if (!qty || isNaN(qty)) return;

    try {
        const res = await api.placeOrder({
            instrument_token: currentInstrumentKey,
            quantity: parseInt(qty),
            transaction_type: side,
            order_type: 'MARKET',
            product: 'I'
        });
        if (res.status === 'success') {
            showToast(`Order placed: ${side} ${qty} qty`, "success");
            setTimeout(() => {
                refreshOrderBook();
                refreshTrades();
                refreshPositions();
            }, 1000);
        }
    } catch (e) {
        showToast(`Order failed: ${e.message}`, "error");
    }
}

async function refreshAccountSummary() {
    try {
        const data = await api.getFunds();
        const funds = data.data || {};
        updateElementText('account-balance', `₹${formatPrice(funds.utilised_margin || 0)}`);
        updateElementText('account-pnl', `₹${formatPrice(funds.pnl || 0)}`);
    } catch (e) {
        console.error("Failed to fetch funds", e);
    }
}

async function refreshPositions() {
    try {
        const res = await api.getPositions();
        const data = res.data || [];
        const list = document.getElementById("positions-body");
        if (!list) return;
        list.innerHTML = "";
        
        if (data.length === 0) {
            list.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-muted)">No open positions</td></tr>`;
            return;
        }
        
        data.forEach(p => {
            const row = document.createElement("tr");
            const pnlClass = p.pnl >= 0 ? "text-success" : "text-danger";
            row.innerHTML = `
                <td class="mono" style="font-size:0.75rem">${p.tradingsymbol}</td>
                <td>${p.quantity}</td>
                <td><span class="badge ${p.quantity > 0 ? 'buy' : 'sell'}">${p.quantity > 0 ? 'BUY' : 'SELL'}</span></td>
                <td class="mono">₹${formatPrice(p.average_price)}</td>
                <td class="mono">₹${formatPrice(p.last_price)}</td>
                <td class="mono ${pnlClass}">₹${formatPrice(p.pnl)}</td>
                <td>
                    <button class="btn btn-outline" style="padding: 2px 8px; font-size: 0.7rem; color: #ff4757; border-color: rgba(255, 71, 87, 0.3);" 
                        onclick="closePosition('${p.instrument_token}', ${Math.abs(p.quantity)}, '${p.quantity > 0 ? 'SELL' : 'BUY'}')">
                        Close
                    </button>
                </td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh positions", e);
    }
}

/**
 * Square off an open position immediately via Market Order.
 */
window.closePosition = async function(instrumentToken, quantity, side) {
    // Extract a readable symbol name if possible
    const symbolParts = instrumentToken.split('|');
    const displaySymbol = symbolParts.length > 1 ? symbolParts[1] : instrumentToken;
    
    if (!confirm(`Are you sure you want to close this position (${quantity} units of ${displaySymbol})?`)) return;
    
    try {
        showToast("Closing position...", "info");
        const res = await api.placeOrder({
            instrument_token: instrumentToken,
            quantity: quantity,
            transaction_type: side,
            order_type: "MARKET"
        });
        
        if (res.status === "success") {
            showToast("Position closure order placed!", "success");
            setTimeout(refreshPositions, 1000);
            setTimeout(refreshOrderBook, 1500);
        } else {
            showToast("Failed to close position: " + (res.message || "Unknown error"), "error");
        }
    } catch (e) {
        console.error("Error closing position:", e);
        showToast("Error closing position", "error");
    }
};

async function refreshOrderBook() {
    try {
        const data = await api.getOrderBook();
        const list = document.getElementById("book-body");
        if (!list) return;
        list.innerHTML = "";
        
        const book = data.data || [];
        if (book.length === 0) {
            list.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-muted)">Order book empty</td></tr>`;
            return;
        }
        
        book.reverse().forEach(o => {
            const row = document.createElement("tr");
            let statusClass = "text-muted";
            const stat = (o.status || "").toLowerCase();
            if (stat.includes("complete")) statusClass = "text-success";
            else if (stat.includes("reject") || stat.includes("cancel")) statusClass = "text-danger";
            else if (stat.includes("open")) statusClass = "text-warning";

            row.innerHTML = `
                <td class="text-muted" style="font-size:0.7rem">${new Date(o.order_timestamp).toLocaleTimeString()}</td>
                <td><span class="badge ${o.transaction_type.toLowerCase()}">${o.transaction_type}</span></td>
                <td class="mono" style="font-size:0.75rem">${o.tradingsymbol}</td>
                <td>${o.quantity}</td>
                <td class="mono">₹${formatPrice(o.average_price || o.price)}</td>
                <td class="${statusClass}" style="font-size:0.75rem">${o.status.toUpperCase()}</td>
                <td class="mono" style="font-size:0.65rem; color:var(--text-muted)">${o.tag || '-'}</td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh order book", e);
    }
}

async function refreshTrades() {
    try {
        const data = await api.getTrades();
        const list = document.getElementById("trades-body");
        if (!list) return;
        list.innerHTML = "";
        
        const trades = data.data || [];
        if (trades.length === 0) {
            list.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px; color:var(--text-muted)">No trades today</td></tr>`;
            return;
        }
        
        trades.reverse().forEach(t => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td class="text-muted" style="font-size:0.7rem">${new Date(t.order_timestamp).toLocaleTimeString()}</td>
                <td class="mono" style="font-size:0.75rem">${t.tradingsymbol}</td>
                <td><span class="badge ${t.transaction_type.toLowerCase()}">${t.transaction_type}</span></td>
                <td>${t.quantity}</td>
                <td class="mono">₹${formatPrice(t.average_price)}</td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh trades", e);
    }
}

async function refreshSignals() {
    try {
        const data = await api.getSignals();
        const list = document.getElementById("signals-body");
        if (!list) return;
        list.innerHTML = "";
        
        const signals = data.data || [];
        if (signals.length === 0) {
            list.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px; color:var(--text-muted)">No signals Generated</td></tr>`;
            return;
        }
        
        signals.reverse().forEach(s => {
            const row = document.createElement("tr");
            const slDisplay = s.stop_loss ? `₹${formatPrice(s.stop_loss)}` : '-';
            const qtyDisplay = s.quantity || '-';
            
            row.innerHTML = `
                <td class="text-muted" style="font-size:0.7rem">${s.timestamp}</td>
                <td class="mono" style="font-size:0.75rem">${s.instrument_key}</td>
                <td><span class="badge ${s.action.toLowerCase()}">${s.action}</span></td>
                <td class="mono">₹${formatPrice(s.price)}</td>
                <td class="mono" style="color:#ff4757">sl: ${slDisplay}</td>
                <td>${qtyDisplay}</td>
                <td>${s.confidence_score !== undefined ? s.confidence_score : '-'}</td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh signals", e);
    }
}

async function refreshStatus() {
    try {
        const data = await api.getStatus();
        updateEngineStatus(data);
    } catch (e) {
        console.error("Failed to refresh status", e);
    }
}

function updateEngineStatus(status) {
    const text = document.getElementById("engine-status-text");
    if (!text) return;
    
    if (status.initialized) {
        text.innerText = "Active";
        text.className = "text-success";
        engineActive = true;
    } else {
        text.innerText = "Not Initialized";
        text.className = "text-warning";
        engineActive = false;
    }
    
    const autoToggle = document.getElementById("toggle-automode");
    if (autoToggle) autoToggle.checked = status.auto_mode;
    
    document.getElementById("auto-mode-card")?.classList.toggle("active", status.auto_mode);
}

async function checkAuth() {
    try {
        const res = await fetch(`${window.location.origin}/health`);
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
        updateElementText("auth-status-text", "API Offline");
    }
}

function updateClock() {
    const clock = document.getElementById('clock');
    if (clock) {
        clock.innerText = new Date().toLocaleTimeString('en-IN') + " IST";
    }
}

function addLog(msg, type = "info") {
    const logViewer = document.getElementById("activity-log");
    if (!logViewer) return;
    const div = document.createElement("div");
    div.className = "log-line";
    const time = new Date().toLocaleTimeString('en-IN', { hour12: false });
    div.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-msg ${type}">${msg}</span>`;
    logViewer.appendChild(div);
    const pnl = document.getElementById('tab-activity');
    if (pnl) pnl.scrollTop = pnl.scrollHeight;
}

// Window globals for legacy onclick handlers
window.selectInstrument = selectInstrument;
window.switchBottomTab = (tabId) => switchTab('bottom-panel', `tab-${tabId}`);
window.setChartTimeframe = (interval) => {
    currentInterval = interval;
    localStorage.setItem("currentInterval", interval);
    
    document.querySelectorAll('[id^="tf-"]').forEach(btn => {
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-outline');
    });
    const activeBtn = document.getElementById(`tf-${interval}`);
    if (activeBtn) {
        activeBtn.classList.remove('btn-outline');
        activeBtn.classList.add('btn-primary');
    }
    
    fetchHistoricalCandles().then(() => refreshOverlay());
};
window.loginUpstox = () => window.location.href = "/auth/login";

window.initializeEngine = async () => {
    try {
        showToast("Initializing Engine...", "info");
        await api.initializeEngine();
        refreshStatus();
    } catch (e) {
        showToast("Initialization failed", "error");
    }
};

window.runCycle = async () => {
    try {
        showToast("Running engine cycle...", "info");
        await api.runCycle();
    } catch (e) {
        showToast("Cycle failed", "error");
    }
};

// window.toggleSidebar removed

window.triggerTestSignal = async () => {
    try {
        showToast("Triggering test signal...", "info");
        await api.triggerTestSignal(currentInstrumentKey);
    } catch (e) {
        showToast("Failed to trigger test signal", "error");
    }
};

window.saveSettings = async () => {
    const key = document.getElementById('setting-api-key').value;
    const secret = document.getElementById('setting-api-secret').value;
    const uri = document.getElementById('setting-redirect-uri').value;
    
    const payload = {};
    if (key && !key.includes('...')) payload.API_KEY = key;
    if (secret && !secret.includes('***')) payload.API_SECRET = secret;
    if (uri) payload.REDIRECT_URI = uri;
    
    try {
        await api.saveSettings(payload);
        showToast("API Configuration Saved", "success");
    } catch (e) {
        showToast("Failed to save settings", "error");
    }
};

window.saveSandboxSettings = async () => {
    const key = document.getElementById('setting-sandbox-key').value;
    const secret = document.getElementById('setting-sandbox-secret').value;
    const token = document.getElementById('setting-sandbox-token').value;
    
    const payload = {};
    if (key && !key.includes('...')) payload.SANDBOX_API_KEY = key;
    if (secret && !secret.includes('***')) payload.SANDBOX_API_SECRET = secret;
    if (token && !token.includes('***')) payload.SANDBOX_ACCESS_TOKEN = token;
    
    try {
        await api.saveSettings(payload);
        showToast("Sandbox Configuration Saved", "success");
    } catch (e) {
        showToast("Failed to save sandbox settings", "error");
    }
};

window.saveInstrumentMetadata = async () => {
    const key = document.getElementById('setting-instr-key').value;
    const lotSize = document.getElementById('setting-instr-lotsize').value;
    const minLot = document.getElementById('setting-instr-minlot').value;
    
    if (!key) {
        showToast("Please enter an instrument key", "warning");
        return;
    }
    
    try {
        showToast(`Updating ${key}...`, "info");
        const url = `${window.location.origin}/market/instruments/${encodeURIComponent(key)}/metadata`;
        const params = new URLSearchParams();
        if (lotSize) params.append('lot_size', lotSize);
        if (minLot) params.append('minimum_lot', minLot);
        
        const res = await fetch(`${url}?${params.toString()}`, { method: 'POST' });
        const data = await res.json();
        
        if (data.status === "success") {
            showToast("Instrument Metadata Updated ✅", "success");
            logActivity(`System: Updated ${key} lot settings.`);
        } else {
            showToast("Failed: " + (data.message || "Unknown error"), "error");
        }
    } catch (e) {
        console.error("Error updating instrument metadata:", e);
        showToast("Error updating metadata", "error");
    }
};

window.saveRiskConfig = async () => {
    const capital = document.getElementById('risk-capital')?.value;
    const risk = document.getElementById('risk-pct')?.value;
    const maxLoss = document.getElementById('risk-maxloss')?.value;
    const maxTrades = document.getElementById('risk-maxtrades')?.value;
    
    const payload = {
        TRADING_CAPITAL: parseFloat(capital) || 100000,
        MAX_RISK_PER_TRADE_PCT: parseFloat(risk) || 1.0,
        MAX_DAILY_LOSS_PCT: parseFloat(maxLoss) || 3.0,
        MAX_OPEN_TRADES: parseInt(maxTrades) || 3
    };
    
    try {
        await api.saveSettings(payload);
        showToast("Risk Configuration Saved ✅", "success");
        refreshStatus();
    } catch (e) {
        showToast("Failed to save risk config", "error");
    }
};

// ── Settings persistence: toggles + save → DB ─────────────────────
window.toggleSandboxMode = async (enabled) => {
    try {
        await api.saveSettings({ USE_SANDBOX: enabled });
        showToast(`Sandbox Mode ${enabled ? 'Enabled' : 'Disabled'}`, 'info');
        // Refresh everything to show correct environment data
        refreshPositions();
        refreshTrades();
        refreshAccountSummary();
    } catch (e) { showToast('Failed to toggle sandbox mode', 'error'); }
};

window.togglePaperMode = async (enabled) => {
    try {
        await api.saveSettings({ PAPER_TRADING: enabled });
        updatePaperBadge(enabled);
        showToast(`Paper Trading ${enabled ? 'Enabled' : 'Disabled'}`, 'info');
        logActivity(`System: Paper Trading ${enabled ? 'ON' : 'OFF'}`);
        // Refresh trades to show paper vs live
        refreshTrades();
    } catch (e) { showToast('Failed to toggle paper trading', 'error'); }
};

window.updateTradingSide = async (side) => {
    try {
        await api.saveSettings({ TRADING_SIDE: side });
        showToast(`Execution side set to ${side}`, 'info');
    } catch (e) { showToast('Failed to update trading side', 'error'); }
};

window.loadSettings = async () => {
    try {
        const s = await api.getSettings();
        // API Credentials
        const apiKey = document.getElementById('setting-api-key');
        if (apiKey && s.API_KEY) apiKey.value = s.API_KEY;
        const redirectUri = document.getElementById('setting-redirect-uri');
        if (redirectUri && s.REDIRECT_URI) redirectUri.value = s.REDIRECT_URI;
        
        // Sandbox
        const sbKey = document.getElementById('setting-sandbox-key');
        if (sbKey && s.SANDBOX_API_KEY) sbKey.value = s.SANDBOX_API_KEY;
        
        // Toggles
        const sandboxToggle = document.getElementById('toggle-sandboxmode');
        if (sandboxToggle) sandboxToggle.checked = !!s.USE_SANDBOX;
        const paperToggle = document.getElementById('toggle-papermode');
        if (paperToggle) paperToggle.checked = s.PAPER_TRADING !== false;
        
        // Trading side
        const sideSelect = document.getElementById('setting-trading-side');
        if (sideSelect && s.TRADING_SIDE) sideSelect.value = s.TRADING_SIDE;
        
        // Risk
        const capital = document.getElementById('risk-capital');
        if (capital && s.TRADING_CAPITAL) capital.value = s.TRADING_CAPITAL;
        const riskPct = document.getElementById('risk-pct');
        if (riskPct && s.MAX_RISK_PER_TRADE_PCT) riskPct.value = s.MAX_RISK_PER_TRADE_PCT;
        const maxLoss = document.getElementById('risk-maxloss');
        if (maxLoss && s.MAX_DAILY_LOSS_PCT) maxLoss.value = s.MAX_DAILY_LOSS_PCT;
        const maxTrades = document.getElementById('risk-maxtrades');
        if (maxTrades && s.MAX_OPEN_TRADES) maxTrades.value = s.MAX_OPEN_TRADES;
    } catch(e) {
        console.error('Failed to load settings', e);
    }
};

window.loadStrategy = async () => {
    const selector = document.getElementById('strategy-selector');
    if (!selector) return;
    
    const strategyClass = selector.options[selector.selectedIndex].dataset.class;
    const name = selector.options[selector.selectedIndex].text;
    
    const payload = {
        strategy_class: strategyClass,
        name: name,
        instruments: currentInstrumentKey,
        timeframe: currentInterval,
        paper_trading: true // Initial default
    };

    try {
        await api.loadStrategy(payload);
        showToast("Strategy Loaded Successfully", "success");
        refreshStatus();
        refreshOverlay();
    } catch (e) {
        showToast("Failed to load strategy", "error");
    }
};

window.toggleAutoMode = async (enabled) => {
    try {
        await api.setAutoMode(enabled);
        showToast(`Auto Mode ${enabled ? 'Enabled' : 'Disabled'}`, 'info');
        refreshStatus();
    } catch (e) {
        showToast("Failed to toggle Auto Mode", "error");
    }
};

// ── Strategy Schema & Overlay (Ported from legacy) ────────────────

async function fetchStrategySchemas() {
    try {
        const data = await api.getStrategySchemas();
        const selector = document.getElementById("strategy-selector");
        if (!selector) return;
        selector.innerHTML = "";
        
        data.strategies.forEach(s => {
            dynamicSchemas[s.id] = s;
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.dataset.class = s.class;
            opt.innerText = s.name;
            selector.appendChild(opt);
        });
        
        // Default select first strategy
        if (selector.options.length > 0) {
            selector.selectedIndex = 0;
            renderDynamicStrategyForm();
            refreshOverlay(); // Initial overlay trigger
        }
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
    if (!container) return;
    container.innerHTML = "";
    
    schema.params.forEach(p => {
        const div = document.createElement('div');
        div.className = "form-group";
        div.style.marginTop = "8px";
        
        if (p.type === 'boolean') {
            div.innerHTML = `
                <label style="display: flex; align-items: center; gap: 8px; font-size: 0.8rem; color: var(--text-secondary);">
                    <input type="checkbox" class="dyn-param" data-name="${p.name}" ${p.default ? 'checked' : ''}> ${p.name.replace(/_/g, ' ')}
                </label>`;
        } else if (p.type === 'number') {
            div.innerHTML = `
                <label class="form-label">${p.name.replace(/_/g, ' ')}</label>
                <input type="number" class="form-input dyn-param" data-name="${p.name}" value="${p.default}" ${p.name.includes('mult') || p.name.includes('pct') ? 'step="0.1"' : ''}>`;
        } else {
            div.innerHTML = `
                <label class="form-label">${p.name.replace(/_/g, ' ')}</label>
                <input type="text" class="form-input dyn-param" data-name="${p.name}" value="${p.default}">`;
        }
        container.appendChild(div);
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

async function refreshOverlay() {
    if (!currentInstrumentKey) return;
    const selector = document.getElementById("strategy-selector");
    let cls = selector?.options[selector.selectedIndex]?.dataset?.class;
    // Fallback to default strategy if selector isn't populated yet
    if (!cls) cls = "SuperTrendPro";
    
    // Show loading state in the HUD
    const hud = document.getElementById("strategy-hud-container");
    if (hud && hud.innerText.includes('Waiting')) {
        hud.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--accent-primary); font-size: 0.8rem;">⏳ Loading strategy data...</div>`;
    }
    
    await fetchStrategyOverlay(currentInstrumentKey, currentInterval, cls, getDynamicParams());
}

async function fetchStrategyOverlay(instrumentKey, interval, strategyClass, params) {
    try {
        const res = await api.getStrategyOverlay(instrumentKey, interval, strategyClass, params);
        if (res.status === "success") {
            if (res.latest_metrics) {
                renderStrategyHUD({ latest_metrics: res.latest_metrics });
            } else {
                renderStrategyHUDEmpty("No metrics returned — strategy may need more candle data.");
            }
            
            if (res.overlay && res.overlay.length > 0) {
                const stData = res.overlay.filter(pt => pt.supertrend !== null).map(pt => ({
                    time: Math.floor(pt.time) + IST_OFFSET,
                    value: pt.supertrend,
                    color: pt.trend === 1 ? '#00d084' : '#ff4757'
                }));
                
                const markers = [];
                let lastTrend = null;
                res.overlay.forEach(pt => {
                    const ds = pt.time;
                    if (lastTrend !== null && pt.trend !== lastTrend) {
                        markers.push({
                            time: Math.floor(ds) + IST_OFFSET,
                            position: pt.trend === 1 ? 'belowBar' : 'aboveBar',
                            color: pt.trend === 1 ? '#00d084' : '#ff4757',
                            shape: pt.trend === 1 ? 'arrowUp' : 'arrowDown',
                            text: pt.trend === 1 ? 'BUY' : 'SELL'
                        });
                    }
                    lastTrend = pt.trend;
                });
                chart.setOverlayData(stData);
                chart.setMarkers(markers);
            }
        } else {
            renderStrategyHUDEmpty(res.message || "Strategy computation failed.");
        }
    } catch (e) {
        console.error("Overlay fetch failed", e);
        renderStrategyHUDEmpty("Network error — check server connection.");
    }
}

function renderStrategyHUDEmpty(message) {
    const hud = document.getElementById("strategy-hud-container");
    if (!hud) return;
    hud.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 0.8rem;">
        <div style="margin-bottom: 4px;">📊 SuperTrend Pro v6.3</div>
        <div style="color: var(--accent-secondary);">${message}</div>
    </div>`;
}

function renderStrategyHUD(strategy) {
    const m = strategy.latest_metrics;
    if (!m) {
        renderStrategyHUDEmpty("No metrics data available.");
        return;
    }
    
    // Helper to format rows identical to TradingView
    const bgHdr = "background: #1e222d; color: white;";
    const bgRow = "background: #2a2e39; color: white;";
    const bgCyn = "background: rgba(0, 188, 212, 0.2); color: #00bcd4;";
    const bgOrn = "background: rgba(255, 152, 0, 0.2); color: #ff9800;";
    
    const cGrn = "background: rgba(76, 175, 80, 0.2); color: #4caf50;";
    const cRed = "background: rgba(244, 67, 54, 0.2); color: #ff5252;";
    const cYlw = "background: rgba(255, 235, 59, 0.2); color: #ffeb3b;";
    
    const passCol = (ok) => ok ? cGrn : cRed;
    
    let html = `<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #404040; font-family: monospace; font-size: 0.75rem;">`;
    
    const addRow = (label, val, bgLabel, bgVal) => {
        html += `<div style="padding: 4px 8px; ${bgLabel}">${label}</div>`;
        html += `<div style="padding: 4px 8px; ${bgVal}">${val}</div>`;
    };
    
    addRow("Metric", "Value", bgHdr, bgHdr);
    
    // Safely render — not all metrics may be present
    if (m.tf_profile) addRow("TF profile", `${m.tf_profile} (${m.tf_mode || ''})`, bgCyn, bgCyn);
    if (m.exit_mode) addRow("Exit mode", m.exit_mode, bgCyn, bgCyn);
    if (m.trend) addRow("ST Trend", m.trend, bgRow, m.trend === "LONG" ? cGrn : cRed);
    
    if (m.hard_gates) {
        if (m.hard_gates.dual_st) addRow("H1 Dual ST", m.hard_gates.dual_st, bgOrn, m.hard_gates.dual_st === "AGREE" ? cGrn : cRed);
        if (m.hard_gates.consecutive) {
            let consecOk = parseInt(m.hard_gates.consecutive.split('/')[0]) >= parseInt(m.hard_gates.consecutive.split('/')[1]);
            addRow("H2 Consec", `${m.hard_gates.consecutive} ${consecOk ? 'PASS' : 'FAIL'}`, bgOrn, passCol(consecOk));
        }
    }
    
    if (m.soft_filters) {
        if (m.soft_filters.score !== undefined) addRow("Soft score", m.soft_filters.score, bgRow, cYlw);
        if (m.soft_filters.adx) addRow("S1 ADX", `${m.soft_filters.adx.value} ${m.soft_filters.adx.pass ? 'PASS' : 'FAIL'}`, bgRow, passCol(m.soft_filters.adx.pass));
        if (m.soft_filters.volume) addRow("S2 Volume", m.soft_filters.volume.pass ? "SURGE" : "FLAT", bgRow, passCol(m.soft_filters.volume.pass));
        if (m.soft_filters.atr_pct) addRow("S3 ATR%", `${m.soft_filters.atr_pct.value}%`, bgRow, passCol(m.soft_filters.atr_pct.pass));
        if (m.soft_filters.roc) addRow("S4 ROC", `${m.soft_filters.roc.value}%`, bgRow, passCol(m.soft_filters.roc.pass));
        if (m.soft_filters.bb_squeeze) {
            let bb = m.soft_filters.bb_squeeze;
            addRow("S5 BB", bb.state, bgRow, bb.pass ? cGrn : (bb.state === 'SQUEEZE' ? cYlw : cRed));
        }
    }
    
    if (m.bars_in_trend !== undefined) addRow("Bars held", m.bars_in_trend, bgRow, bgRow);
    
    html += `</div>`;
    document.getElementById("strategy-hud-container").innerHTML = html;
}
window.switchMainView = (view) => {
    // Hide all
    const views = ['tvchart', 'option-chain-container', 'settings-view-container'];
    views.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    
    // Update button states
    const btnMap = {
        'chart': 'btn-view-chart',
        'options': 'btn-view-options',
        'settings': 'btn-view-settings-center'
    };
    Object.values(btnMap).forEach(bid => {
        document.getElementById(bid)?.classList.replace('btn-primary', 'btn-outline');
    });

    // Show target
    const targetMap = {
        'chart': 'tvchart',
        'options': 'option-chain-container',
        'settings': 'settings-view-container'
    };
    const targetId = targetMap[view];
    const targetEl = document.getElementById(targetId);
    if (targetEl) {
        targetEl.style.display = 'block';
        const btn = document.getElementById(btnMap[view]);
        if (btn) {
            btn.classList.remove('btn-outline');
            btn.classList.add('btn-primary');
        }
    }

    // Persist instrument name across view transitions
    updateElementText('current-instrument', currentInstrumentName);
    updateElementText('oc-instrument-name', currentInstrumentName);

    if (view === 'options') fetchOptionChain();
    if (view === 'settings') refreshStatus();
};

window.switchSettingsTab = (tabId) => {
    // Hide all contents
    document.querySelectorAll('.settings-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
    
    // Show target
    document.getElementById(`set-tab-${tabId}`)?.classList.add('active');
    // Find button (hacky but works)
    document.querySelectorAll('.settings-tab').forEach(btn => {
        if (btn.innerText.toLowerCase().includes(tabId)) btn.classList.add('active');
    });
};

window.fetchOptionChain = async () => {
    const expiry = document.getElementById('oc-expiry-select')?.value;
    updateElementText('oc-instrument-name', currentInstrumentName);
    try {
        const res = await api.getOptionChain(currentInstrumentKey, expiry);
        if (res.status === 'success') {
            renderOptionChain(res);
        }
    } catch (e) {
        showToast("Failed to fetch option chain", "error");
    }
};

function renderOptionChain(data) {
    const tbody = document.getElementById('oc-tbody');
    if (!tbody) return;
    tbody.innerHTML = "";
    
    // Hide placeholder
    const placeholder = document.getElementById('oc-placeholder');
    if (placeholder) placeholder.style.display = 'none';
    
    // Populate expiries if not already
    const select = document.getElementById('oc-expiry-select');
    if (select && select.options.length <= 1 && data.available_expiries) {
        data.available_expiries.forEach(exp => {
            const opt = document.createElement('option');
            opt.value = exp;
            opt.innerText = exp;
            if (exp === data.expiry_date) opt.selected = true;
            select.appendChild(opt);
        });
    }

    if (!data.chain || data.chain.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" style="padding:40px;text-align:center;color:var(--text-muted)">No data for this expiry</td></tr>`;
        return;
    }

    // Find ATM
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
        const tr = document.createElement('tr');
        const ce = row.ce || {};
        const pe = row.pe || {};
        const isATM = idx === atmIndex;
        
        const ceITM = row.strike_price < data.spot_price;
        const peITM = row.strike_price > data.spot_price;
        
        if (isATM) {
            tr.style.border = "1px solid #00d084";
            tr.id = "atm-row";
        }
        
        tr.innerHTML = `
            <td style="color:var(--text-muted); font-size:0.7rem;">${(ce.delta || 0).toFixed(2)}</td>
            <td style="color:var(--text-muted); font-size:0.7rem;">${(ce.theta || 0).toFixed(2)}</td>
            <td style="color:var(--text-muted);">${(ce.iv || 0).toFixed(1)}%</td>
            <td style="width: 60px;">
                <div style="font-size:0.65rem; color: #8b8b9e;">${(ce.volume || 0).toLocaleString()}</div>
                <div style="height:2px; background:#00d084; width:${Math.min(100, (ce.volume || 0)/1000)}%; opacity:0.5;"></div>
            </td>
            <td style="font-weight:600; color:#10b981; background:${ceITM ? 'rgba(16,185,129,0.08)' : 'transparent'}">${ce.ltp ? formatPrice(ce.ltp) : '-'}</td>
            <td style="background:var(--bg-dark); font-weight:700; border-left:1px solid var(--border-color); border-right:1px solid var(--border-color);">${row.strike_price}</td>
            <td style="font-weight:600; color:#ef4444; background:${peITM ? 'rgba(239,68,68,0.08)' : 'transparent'}">${pe.ltp ? formatPrice(pe.ltp) : '-'}</td>
            <td style="width: 60px;">
                <div style="font-size:0.65rem; color: #8b8b9e;">${(pe.volume || 0).toLocaleString()}</div>
                <div style="height:2px; background:#ef4444; width:${Math.min(100, (pe.volume || 0)/1000)}%; opacity:0.5;"></div>
            </td>
            <td style="color:var(--text-muted);">${(pe.iv || 0).toFixed(1)}%</td>
            <td style="color:var(--text-muted); font-size:0.7rem;">${(pe.theta || 0).toFixed(2)}</td>
            <td style="color:var(--text-muted); font-size:0.7rem;">${(pe.delta || 0).toFixed(2)}</td>
        `;
        tbody.appendChild(tr);
    });

    // Auto-scroll to ATM
    setTimeout(() => {
        const atm = document.getElementById("atm-row");
        if (atm) atm.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 100);
}

function setupGlobalSearch() {
    const modal = document.getElementById("global-search-modal");
    const input = document.getElementById("global-search-input");
    const results = document.getElementById("global-search-results");
    
    if (!modal || !input) return;

    // 1. Opening the modal on keyboard press
    window.addEventListener("keydown", (e) => {
        const isFocus = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
        if (isFocus) {
            if (e.key === "Escape") {
                modal.classList.remove("active");
                document.activeElement.blur();
            }
            return;
        }
        // Only trigger search on plain alphanumeric keys — NOT when Cmd/Ctrl/Alt are held
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (/^[a-z0-9\/]$/i.test(e.key)) {
            modal.classList.add("active");
            input.value = "";
            input.focus();
            selectedSearchIndex = -1;
            globalSearchResults = [];
            results.innerHTML = "";
        }
    });

    // 2. Navigation & Selection in Input
    input.addEventListener("keydown", (e) => {
        if (!modal.classList.contains("active")) return;

        const count = globalSearchResults.length;
        if (e.key === "ArrowDown") {
            e.preventDefault();
            selectedSearchIndex = count > 0 ? (selectedSearchIndex + 1) % count : -1;
            renderGlobalSearchResults(globalSearchResults);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            selectedSearchIndex = count > 0 ? (selectedSearchIndex - 1 + count) % count : -1;
            renderGlobalSearchResults(globalSearchResults);
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (selectedSearchIndex >= 0 && globalSearchResults[selectedSearchIndex]) {
                const inst = globalSearchResults[selectedSearchIndex];
                selectInstrument(inst.instrument_key, (inst.name || inst.trading_symbol));
                modal.classList.remove("active");
            }
        }
    });

    let timer;
    input.oninput = (e) => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const query = e.target.value;
            if (query.length < 2) {
                globalSearchResults = [];
                results.innerHTML = "";
                return;
            }
            try {
                const data = await api.searchInstruments(query);
                globalSearchResults = data.instruments || [];
                selectedSearchIndex = globalSearchResults.length > 0 ? 0 : -1;
                renderGlobalSearchResults(globalSearchResults);
            } catch (err) {}
        }, 150);
    };

    modal.onclick = (e) => {
        if (e.target === modal) modal.classList.remove("active");
    };
}

function renderGlobalSearchResults(instruments) {
    const container = document.getElementById("global-search-results");
    if (!container) return;
    container.innerHTML = "";

    instruments.forEach((inst, idx) => {
        const item = document.createElement("div");
        item.className = "search-result-item" + (idx === selectedSearchIndex ? " selected" : "");
        item.onclick = () => {
            selectInstrument(inst.instrument_key, name);
            document.getElementById("global-search-modal").classList.remove("active");
        };

        const symbol = inst.trading_symbol || inst.symbol || "Unknown";
        const name = inst.name || symbol;

        item.innerHTML = `
            <div>
                <span class="symbol">${symbol}</span>
                <span class="name">${name}</span>
            </div>
            <div class="exchange">${inst.segment || inst.exchange || ""}</div>
        `;
        if (idx === selectedSearchIndex) {
            item.scrollIntoView({ block: 'nearest' });
        }
        container.appendChild(item);
    });
}
async function refreshMarketStatus() {
    try {
        const res = await api.getMarketStatus("NSE");
        const el = document.getElementById("market-status-badge");
        if (!el) return;
        
        // Handle both {status: "success", data: ...} and {data: ...}
        const data = res.data || res;
        if (data && data.status) {
            const s = data.status;
            el.innerText = s.replace(/_/g, " ");
            el.className = s.includes("OPEN") ? "badge badge-success" : "badge badge-error";
        } else {
            el.innerText = "STATUS UNKNOWN";
            el.className = "badge";
        }
    } catch (e) {
        console.error("Failed to refresh market status", e);
    }
}
