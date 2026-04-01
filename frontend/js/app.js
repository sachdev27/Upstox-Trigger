/**
 * app.js — Main application logic (ES Module).
 */

import { api } from './api.js';
import { showToast, updateElementText, formatPrice } from './ui.js';
import { EngineWS } from './ws.js';
import { ChartManager } from './chart.js';

// Application State
let currentInstrumentKey = localStorage.getItem("currentInstrumentKey") || "NSE_INDEX|Nifty 50";
let currentInstrumentName = localStorage.getItem("currentInstrumentName") || "Nifty 50";
let currentInterval = localStorage.getItem("currentInterval") || "15minute";
let engineActive = false;
let dynamicSchemas = {};
const IST_OFFSET = 0; // Standardize to UTC seconds

let globalSearchResults = [];
let selectedSearchIndex = -1;
const domNodes = new Map(); // Performance Cache: instrument_key -> { row: HTMLElement, ltp: HTMLElement, pnl: HTMLElement, ... }
const lastUiUpdate = { status: 0, volume: 0 }; // Throttling state
const pendingUpdates = new Map(); // Batching Queue: key -> last_tick_data
let isFlushing = false;

// --- IndexedDB Cache System ---
const DB_NAME = 'TradingTerminalDB';
const DB_VERSION = 1;
const STORE_NAME = 'historical_data';

async function initDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME);
            }
        };
        request.onsuccess = (e) => resolve(e.target.result);
        request.onerror = (e) => reject(e.target.error);
    });
}

async function getCachedHistorical(key, interval) {
    const db = await initDB();
    return new Promise((resolve) => {
        const transaction = db.transaction(STORE_NAME, 'readonly');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.get(`${key}_${interval}`);
        request.onsuccess = () => {
            const result = request.result;
            if (result && (Date.now() - result.timestamp < 300000)) { // 5 minute TTL
                resolve(result.data);
            } else {
                resolve(null);
            }
        };
        request.onerror = () => resolve(null);
    });
}

async function setCachedHistorical(key, interval, data) {
    const db = await initDB();
    const transaction = db.transaction(STORE_NAME, 'readwrite');
    const store = transaction.objectStore(STORE_NAME);
    store.put({ timestamp: Date.now(), data }, `${key}_${interval}`);
}

// Services
const chart = new ChartManager('tvchart');
const ws = new EngineWS(handleWsMessage, () => {
    if (currentInstrumentKey) {
        ws.send({ action: 'subscribe', instrument_key: currentInstrumentKey });
    }
});

document.addEventListener("DOMContentLoaded", async () => {
    chart.init();
    ws.connect();

    // Restore sidebar state — REMOVED

    await fetchHistoricalCandles();
    await refreshAccountSummary();
    await refreshPositions();
    await refreshTrades();
    await refreshSignals();
    await refreshActiveSignals();
    await refreshWatchlist();
    await checkAuth();
    await refreshStatus();
    await loadSettingsIntoUI();
    refreshAccountSummary();
    refreshMarketStatus();
    refreshOrderBook();
    updateClock();
    await fetchStrategySchemas();

    // Restore UI from localStorage
    updateElementText('current-instrument', currentInstrumentName);
    updateElementText('oc-instrument-name', currentInstrumentName);
    updateElementText('inst-ltp', `₹0.00`);

    // Set active state for persisted timeframe
    const activeBtn = document.getElementById(`tf-${currentInterval}`);
    if (activeBtn) {
        activeBtn.classList.remove('btn-outline');
        activeBtn.classList.add('btn-primary');
    }

    // Set up listeners
    setupEventListeners();
    setupGlobalSearch();

    // Interval updates
    setInterval(updateClock, 1000);
    setInterval(refreshAccountSummary, 60000);
    setInterval(refreshPositions, 30000);
    setInterval(refreshMarketStatus, 60000);
    setInterval(refreshOrderBook, 30000);
    setInterval(refreshActiveSignals, 15000); // Every 15 seconds
    setInterval(refreshOverlay, 30000); // Refresh strategy indicators/HUD every 30s
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

    // Watchlist search
    const wlSearch = document.getElementById("watchlist-search");
    let wlSearchTimer;
    wlSearch?.addEventListener("input", (e) => {
        clearTimeout(wlSearchTimer);
        const query = e.target.value.trim();
        const results = document.getElementById("watchlist-search-results");
        if (query.length < 2) {
            results.innerHTML = "";
            return;
        }
        wlSearchTimer = setTimeout(async () => {
            try {
                const data = await api.searchInstruments(query);
                const instruments = data.instruments || [];
                results.innerHTML = "";
                instruments.slice(0, 5).forEach(inst => {
                    const item = document.createElement("div");
                    item.className = "wl-search-item";
                    item.innerHTML = `<span style="font-weight:600; color:var(--primary); font-size:0.8rem;">${inst.trading_symbol || inst.symbol}</span> <span style="font-size:0.7rem; color:var(--text-muted);">${inst.name || ''}</span>`;
                    item.onclick = async () => {
                        await api.addToWatchlist(inst.instrument_key);
                        showToast(`Added ${inst.trading_symbol || inst.symbol} to watchlist`, 'success');
                        wlSearch.value = "";
                        results.innerHTML = "";
                        refreshWatchlist();
                    };
                    results.appendChild(item);
                });
            } catch (e) { console.error(e); }
        }, 300);
    });
}

function handleWsMessage(msg) {
    let d;
    if (Array.isArray(msg) && msg[0] === 't') {
        // Packed format: ["t", key, ltp, v, iv, delta, theta, ts]
        d = {
            instrument_key: msg[1],
            ltp: msg[2],
            volume: msg[3],
            iv: msg[4],
            delta: msg[5],
            theta: msg[6],
            candle: { time: msg[7], close: msg[2], open: msg[2], high: msg[2], low: msg[2] }
        };
    } else if (msg.type === "market_data") {
        d = msg.data;
    }

    if (d) {
        // Queue for batched UI update
        pendingUpdates.set(d.instrument_key, d);
        if (!isFlushing) {
            isFlushing = true;
            requestAnimationFrame(flushUpdates);
        }
    } else if (msg.type === "portfolio_update") {
        refreshPositions();
        refreshAccountSummary();
    } else {
        // Handle named message types
        switch (msg.type) {
            case 'status':
                updateEngineStatus(msg.data);
                break;
            case 'new_signal':
                addLog(`🎯 Signal: ${msg.data.action} on ${msg.data.instrument}`, 'info');
                showToast(`New Signal: ${msg.data.action} on ${msg.data.instrument}`);
                refreshSignals();
                refreshActiveSignals();
                if (msg.data.latest_metrics) {
                    renderStrategyHUD({ latest_metrics: msg.data.latest_metrics });
                }
                refreshOverlay(); // Still refresh overlay for markers and latest indicators on chart
                break;
            case 'trade_executed':
                addLog(`💰 Trade: ${msg.data.action} @ ${msg.data.price}`, 'success');
                showToast(`Trade Executed: ${msg.data.action}`, 'success');
                refreshTrades();
                refreshPositions();
                break;
        }
    }
}

function flushUpdates() {
    isFlushing = false;
    const items = Array.from(pendingUpdates.values());
    pendingUpdates.clear();

    items.forEach(d => {
        // 1. Update Chart (Primary)
        if (d.instrument_key === currentInstrumentKey) {
            chart.updateCandle(d.candle, currentInterval);
            updateElementText("inst-ltp", `₹${formatPrice(d.ltp)}`);
            if (Date.now() - lastUiUpdate.volume > 500) {
                updateElementText("inst-volume", `Vol: ${(d.volume || 0).toLocaleString()}`);
                lastUiUpdate.volume = Date.now();
            }
        }

        // 2. Status Bar
        if (d.instrument_key.includes("NSE_INDEX") && Date.now() - lastUiUpdate.status > 1000) {
            const indicator = document.getElementById("market-status-indicator");
            if (indicator) {
                const name = d.instrument_key.includes("Nifty 50") ? "NIFTY 50" : "BANK NIFTY";
                const currentText = indicator.innerText;
                const statusPart = currentText.includes("|") ? currentText.split("|")[0].trim() : "🟢 Market";
                indicator.innerHTML = `${statusPart} | <span class="mono" style="color:var(--primary); font-weight:600;">${name}: ${formatPrice(d.ltp)}</span>`;
                lastUiUpdate.status = Date.now();
            }
        }

        // 3. Positions (Cached)
        const cachedPos = domNodes.get(`pos-${d.instrument_key}`);
        if (cachedPos) {
            if (cachedPos.ltp) {
                cachedPos.ltp.innerText = `₹${formatPrice(d.ltp)}`;
            }
            updatePositionPnL(cachedPos, d.ltp);
        }

        // 4. Option Chain (Cached)
        const cachedOC = domNodes.get(`oc-${d.instrument_key}`);
        if (cachedOC) {
            if (cachedOC.ltp) cachedOC.ltp.innerText = formatPrice(d.ltp);
            if (cachedOC.volume) cachedOC.volume.innerText = (d.volume || 0).toLocaleString();
            if (cachedOC.delta && d.delta !== undefined) cachedOC.delta.innerText = d.delta.toFixed(2);
            if (cachedOC.theta && d.theta !== undefined) cachedOC.theta.innerText = d.theta.toFixed(2);
            if (cachedOC.iv && d.iv !== undefined) cachedOC.iv.innerText = `${(d.iv || 0).toFixed(1)}%`;
        }
    });

    // PnL Global update
    updateGlobalPnL();
}

function updateGlobalPnL() {
    let totalPnL = 0;
    for (const [key, cached] of domNodes.entries()) {
        if (key.startsWith('pos-')) {
            const pnl = parseFloat(cached.pnl.innerText.replace('₹', '').replace(/,/g, '')) || 0;
            totalPnL += pnl;
        }
    }

    const pnlEl = document.getElementById('account-pnl');
    if (pnlEl) {
        pnlEl.innerText = `₹${formatPrice(totalPnL)}`;
        pnlEl.className = `mono ${totalPnL >= 0 ? 'text-success' : 'text-danger'}`;
    }
}

// Helper function to update PnL for position rows
function updatePositionPnL(container, ltp) {
    // container can be a DOM row or a cached object { pnl: HTMLElement, avg: number, qty: number }
    const isCached = !container.querySelector;
    const pnlCell = isCached ? container.pnl : container.querySelector('.pnl-cell');
    const avg = isCached ? container.avg : parseFloat(container.dataset.avg);
    const qty = isCached ? container.qty : parseFloat(container.dataset.qty);

    if (pnlCell && avg !== undefined && qty !== undefined) {
        if (qty !== 0) {
            const pnl = (ltp - avg) * qty;
            pnlCell.innerText = `₹${formatPrice(pnl)}`;
            pnlCell.className = `mono pnl-cell ${pnl >= 0 ? 'text-success' : 'text-danger'}`;
        }
    }
}


async function fetchHistoricalCandles() {
    try {
        // 1. Check IndexedDB Cache
        const cached = await getCachedHistorical(currentInstrumentKey, currentInterval);
        if (cached && cached.length > 0) {
            chart.setData(cached);
            return;
        }

        // 2. Fetch from API
        const data = await api.getHistoricalCandles(currentInstrumentKey, currentInterval);
        if (data && data.candles) {
            const valid = data.candles
                .filter(c => c && c.time && c.open != null && c.high != null && c.low != null && c.close != null)
                .map(c => ({...c, time: c.time}))
                .sort((a, b) => a.time - b.time);

            const unique = [];
            let lastT = null;
            for (const c of valid) {
                if (c.time !== lastT) {
                    unique.push(c);
                    lastT = c.time;
                }
            }

            // 3. Store in Cache & Update Chart
            if (unique.length > 0) {
                await setCachedHistorical(currentInstrumentKey, currentInterval, unique);
            }
            chart.setData(unique);

            if (unique.length === 0) {
                showToast("No candle data found for this interval", "warning");
            }
        }
    } catch (e) {
        console.error("Failed to fetch candles", e);
    }
}




async function selectInstrument(key, name) {
    const oldKey = currentInstrumentKey;

    // 1. Update state
    currentInstrumentKey = key;
    currentInstrumentName = name;
    localStorage.setItem("currentInstrumentKey", key);
    localStorage.setItem("currentInstrumentName", name);

    updateElementText('current-instrument', name);
    updateElementText('oc-instrument-name', name);
    updateElementText('inst-ltp', '₹--');
    updateElementText('inst-volume', 'Vol: --');

    // 2. Unsubscribe from old if appropriate
    if (oldKey && shouldUnsubscribe(oldKey)) {
        ws.send({ action: 'unsubscribe', instrument_key: oldKey });
    }

    // 3. Subscribe to new
    ws.send({ action: 'subscribe', instrument_key: currentInstrumentKey });

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

    const qty = prompt(`Enter Quantity for ${side} ${currentInstrumentName}:`, "1");
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
                refreshTrades();
                refreshPositions();
                refreshOrderBook();
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
        // Use available_margin for a more useful "Capital" display
        updateElementText('account-balance', `₹${formatPrice(funds.available_margin || 0)}`);
    } catch (e) {
        console.error("Failed to fetch funds", e);
    }
}

async function refreshPositions() {
    try {
        const { data } = await api.getPositions();
        const list = document.getElementById("positions-body");
        if (!list) return;

        // Clear only position-related cache
        for (const key of domNodes.keys()) {
            if (key.startsWith('pos-')) domNodes.delete(key);
        }

        list.innerHTML = "";

        if (!data || data.length === 0) {
            list.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-muted)">No open positions</td></tr>`;
            return;
        }

        let totalPnL = 0;
        data.forEach(p => {
            totalPnL += (p.pnl || 0);
            const row = document.createElement("tr");
            row.dataset.key = p.instrument_token;
            row.dataset.avg = p.average_price;
            row.dataset.qty = p.quantity;

            const pnlClass = p.pnl >= 0 ? "text-success" : "text-danger";
            row.innerHTML = `
                <td class="mono" style="font-size:0.75rem">${p.tradingsymbol}</td>
                <td>${p.quantity}</td>
                <td><span class="badge ${p.quantity > 0 ? 'buy' : 'sell'}">${p.quantity > 0 ? 'BUY' : 'SELL'}</span></td>
                <td class="mono">₹${formatPrice(p.average_price)}</td>
                <td class="mono ltp-cell">₹${formatPrice(p.last_price)}</td>
                <td class="mono pnl-cell ${pnlClass}">₹${formatPrice(p.pnl)}</td>
            `;
            list.appendChild(row);

            // Cache the row and key cells for fast WS updates
            domNodes.set(`pos-${p.instrument_token}`, {
                pnl: row.querySelector('.pnl-cell'),
                ltp: row.querySelector('.ltp-cell'),
                avg: p.average_price,
                qty: p.quantity
            });
        });

        // Update Global PnL in header
        updateGlobalPnL();

        // Trigger dynamic subscription if this tab is active
        const activeTab = document.querySelector('.bottom-tab.active');
        if (activeTab && activeTab.innerText.toLowerCase().includes('position')) {
            subscribeToPositions();
        }
    } catch (e) {
        console.error("Failed to refresh positions", e);
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
            row.innerHTML = `
                <td class="text-muted" style="font-size:0.7rem">${s.timestamp}</td>
                <td class="mono" style="font-size:0.75rem">${s.instrument_key}</td>
                <td><span class="badge ${s.action.toLowerCase()}">${s.action}</span></td>
                <td class="mono">₹${formatPrice(s.price)}</td>
                <td>${s.strategy_name}</td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh signals", e);
    }
}

async function refreshOrderBook() {
    try {
        const { data } = await api.getOrderBook();
        const list = document.getElementById("order-book-body");
        if (!list) return;
        list.innerHTML = "";

        if (!data || data.length === 0) {
            list.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--text-muted)">No orders today</td></tr>`;
            return;
        }

        data.reverse().forEach(o => {
            const row = document.createElement("tr");
            const sideClass = o.transaction_type === 'BUY' ? 'buy' : 'sell';
            const statusClass = (o.status === 'COMPLETE' || o.status === 'FILLED') ? 'text-success' : (o.status === 'REJECTED' || o.status === 'CANCELLED') ? 'text-danger' : 'text-warning';

            row.innerHTML = `
                <td class="text-muted small">${new Date(o.order_timestamp).toLocaleTimeString()}</td>
                <td class="mono small">${o.tradingsymbol}</td>
                <td><span class="badge ${sideClass}">${o.transaction_type}</span></td>
                <td>${o.quantity}</td>
                <td class="mono">₹${formatPrice(o.average_price || o.price)}</td>
                <td class="${statusClass} small">${o.status}</td>
                <td class="text-muted small">${o.status_message || '-'}</td>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("Failed to refresh order book", e);
    }
}

async function refreshMarketStatus() {
    try {
        const data = await api.getMarketStatus();
        const indicator = document.getElementById("market-status-indicator");
        if (indicator) {
            indicator.innerText = data.market_open ? "🟢 Market Open" : "🔴 Market Closed";
            indicator.className = data.market_open ? "text-success" : "text-muted";
        }
    } catch (e) {
        console.error("Failed to refresh market status", e);
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

/**
 * Logic to determine if an instrument should be unsubscribed.
 * @param {string} key - The instrument key to check.
 * @param {boolean} leavingPositions - Force unsubscribe if leaving the positions tab.
 * @returns {boolean} - True if it's safe to unsubscribe.
 */
function shouldUnsubscribe(key, leavingPositions = false) {
    // Never unsubscribe from the active chart instrument
    if (key === currentInstrumentKey) return false;

    // Check if it's visible in the active Positions tab
    // If leavingPositions is true, we ignore the 'active' class on the tab
    if (!leavingPositions) {
        const activeTab = document.querySelector('.bottom-tab.active');
        const isPositionsActive = activeTab && activeTab.innerText.toLowerCase().includes('position');
        if (isPositionsActive) {
            const inPositions = document.querySelector(`#positions-body tr[data-key="${key}"]`);
            if (inPositions) return false;
        }
    }

    return true;
}

function unsubscribeFromPositions() {
    if (!ws || !ws.isConnected()) return;
    const rows = document.querySelectorAll('#positions-body tr[data-key]');
    rows.forEach(row => {
        const key = row.dataset.key;
        if (key && shouldUnsubscribe(key, true)) {
            ws.send({ action: 'unsubscribe', instrument_key: key });
        }
    });
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

function subscribeToPositions() {
    if (!ws || !ws.isConnected()) return;
    const rows = document.querySelectorAll('#positions-body tr[data-key]');
    rows.forEach(row => {
        const key = row.dataset.key;
        if (key) {
            ws.send({ action: 'subscribe', instrument_key: key });
        }
    });
}

// ── Watchlist Management ──────────────────────────────────────

const AVAILABLE_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1H', '1D'];

async function refreshWatchlist() {
    try {
        const data = await api.getWatchlist();
        const container = document.getElementById("watchlist-items");
        if (!container) return;

        const items = data.data || [];
        // Also refresh the name map for active signals
        _watchlistNameMap = {};
        items.forEach(i => {
            _watchlistNameMap[i.instrument_key] = { symbol: i.symbol, name: i.name };
        });

        if (items.length === 0) {
            container.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-muted); font-size: 0.8rem;">No instruments in watchlist</div>';
            return;
        }

        container.innerHTML = "";
        items.forEach(item => {
            const activeTFs = item.timeframes || ['15m'];
            const tfBadges = AVAILABLE_TIMEFRAMES.map(tf => {
                const isActive = activeTFs.includes(tf);
                const cls = isActive ? 'tf-active' : 'tf-inactive';
                const escapedTFs = JSON.stringify(activeTFs).replace(/"/g, '&quot;');
                return '<span class="tf-badge ' + cls + '" onclick="event.stopPropagation(); toggleWatchlistTF(' + item.id + ', \'' + tf + '\', ' + escapedTFs + ')" title="' + (isActive ? 'Remove' : 'Add') + ' ' + tf + '">' + tf + '</span>';
            }).join('');

            const symSafe = (item.symbol || item.name || '').replace(/'/g, "\\'");
            const div = document.createElement("div");
            div.className = "watchlist-item";
            div.innerHTML = '<div style="flex: 1; min-width: 0;">'
                + '<div style="display: flex; align-items: baseline; gap: 6px;">'
                + '<span style="font-size: 0.82rem; font-weight: 600;">' + (item.symbol || item.instrument_key) + '</span>'
                + '<span style="font-size: 0.65rem; color: var(--text-muted);">' + (item.name || '') + '</span>'
                + '</div>'
                + '<div class="tf-picker" style="display: flex; gap: 2px; margin-top: 4px;">' + tfBadges + '</div>'
                + '</div>'
                + '<div style="display: flex; gap: 4px; align-items: center; flex-shrink: 0;">'
                + '<button class="btn btn-outline" style="width: auto; padding: 2px 6px; font-size: 0.6rem;" onclick="event.stopPropagation(); selectInstrument(\'' + item.instrument_key + '\', \'' + symSafe + '\')" title="View Chart">📈</button>'
                + '<button class="btn btn-outline" style="width: auto; padding: 2px 6px; font-size: 0.6rem; color: var(--danger);" onclick="event.stopPropagation(); removeFromWatchlist(\'' + item.instrument_key + '\')" title="Remove">✕</button>'
                + '</div>';
            container.appendChild(div);
        });
    } catch (e) {
        console.error("Failed to refresh watchlist", e);
    }
}

window.toggleWatchlistTF = async (itemId, tf, currentTFs) => {
    let newTFs;
    if (currentTFs.includes(tf)) {
        if (currentTFs.length <= 1) {
            showToast('Must have at least one timeframe', 'warning');
            return;
        }
        newTFs = currentTFs.filter(t => t !== tf);
    } else {
        newTFs = [...currentTFs, tf];
    }
    try {
        await api.updateWatchlistTimeframes(itemId, newTFs.join(','));
        refreshWatchlist();
    } catch (e) {
        showToast('Failed to update timeframes', 'error');
    }
};

window.addCurrentToWatchlist = async () => {
    if (!currentInstrumentKey) {
        showToast('No instrument selected', 'warning');
        return;
    }
    try {
        await api.addToWatchlist(currentInstrumentKey);
        showToast(`Added ${currentInstrumentName} to watchlist`, 'success');
        refreshWatchlist();
    } catch (e) {
        showToast('Failed to add to watchlist', 'error');
    }
};

window.removeFromWatchlist = async (key) => {
    try {
        await api.removeFromWatchlist(key);
        showToast('Removed from watchlist', 'info');
        refreshWatchlist();
    } catch (e) {
        showToast('Failed to remove', 'error');
    }
};

window.importWatchlistCSV = () => {
    document.getElementById('watchlist-csv-input')?.click();
};

window.handleWatchlistCSVImport = async (input) => {
    if (!input.files || !input.files[0]) return;
    try {
        const res = await api.importWatchlist(input.files[0]);
        showToast(`Imported: ${res.added} added, ${res.skipped} skipped`, 'success');
        refreshWatchlist();
    } catch (e) {
        showToast('CSV import failed', 'error');
    }
    input.value = ''; // Reset input
};

window.exportWatchlistCSV = async () => {
    try {
        const response = await api.exportWatchlist();
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'watchlist.csv';
        a.click();
        URL.revokeObjectURL(url);
        showToast('Watchlist exported', 'success');
    } catch (e) {
        showToast('Export failed', 'error');
    }
};

// ── Active Signals ────────────────────────────────────────────

// Cached watchlist lookup for resolving instrument names
let _watchlistNameMap = {};

async function _refreshWatchlistNameMap() {
    try {
        const wlData = await api.getWatchlist();
        const items = wlData.data || [];
        _watchlistNameMap = {};
        items.forEach(i => {
            _watchlistNameMap[i.instrument_key] = { symbol: i.symbol, name: i.name };
        });
    } catch (e) { /* ignore */ }
}

function _resolveInstrument(instrumentKey) {
    const cached = _watchlistNameMap[instrumentKey];
    if (cached) return cached;
    // Fallback: extract from key
    const parts = instrumentKey.split("|");
    return { symbol: parts.length > 1 ? parts[1] : instrumentKey, name: '' };
}

async function refreshActiveSignals() {
    try {
        const data = await api.getActiveSignals();
        const signals = data.data || [];

        // Ensure name map is populated
        if (Object.keys(_watchlistNameMap).length === 0) {
            await _refreshWatchlistNameMap();
        }

        // Build rows HTML once, apply to both tables
        const targets = ['active-signals-body', 'active-signals-body-main'];

        targets.forEach(targetId => {
            const tbody = document.getElementById(targetId);
            if (!tbody) return;
            tbody.innerHTML = "";

            if (signals.length === 0) {
                tbody.innerHTML = `<tr><td colspan="12" style="text-align:center; padding:20px; color:var(--text-muted)">No active signals</td></tr>`;
                return;
            }

            signals.forEach(s => {
                const row = document.createElement("tr");
                if (s.status === 'active') row.classList.add('active-signal-row');

                const inst = _resolveInstrument(s.instrument_key);
                const time = s.created_at ? new Date(s.created_at).toLocaleTimeString('en-IN', { hour12: false }) : '--';
                const statusBadge = s.status === 'active'
                    ? '<span class="badge" style="background:rgba(0,208,132,0.15); color:#00d084;">ACTIVE</span>'
                    : '<span class="badge" style="background:rgba(139,139,158,0.15); color:#8b8b9e;">CLOSED</span>';

                row.innerHTML = `
                    <td class="text-muted" style="font-size:0.7rem">${time}</td>
                    <td style="font-size:0.75rem">${s.strategy_name}</td>
                    <td class="mono" style="font-size:0.75rem; font-weight:600; color:var(--primary);">${inst.symbol}</td>
                    <td style="font-size:0.7rem; color:var(--text-muted);">${inst.name || '-'}</td>
                    <td><span class="badge" style="font-size:0.6rem; padding:1px 4px;">${s.timeframe || '15m'}</span></td>
                    <td><span class="badge ${s.action.toLowerCase()}">${s.action}</span></td>
                    <td class="mono">₹${formatPrice(s.price)}</td>
                    <td class="mono text-muted" style="font-size:0.75rem">${s.stop_loss ? '₹' + formatPrice(s.stop_loss) : '-'}</td>
                    <td class="mono text-muted" style="font-size:0.75rem">${s.take_profit ? '₹' + formatPrice(s.take_profit) : '-'}</td>
                    <td>${s.confidence_score || 0}</td>
                    <td>${statusBadge}</td>
                    <td>
                        ${s.status === 'active' ? `<button class="btn btn-outline" style="width:auto; padding:2px 6px; font-size:0.65rem;" onclick="closeActiveSignal(${s.id})">Close</button>` : ''}
                        <button class="btn btn-outline" style="width:auto; padding:2px 6px; font-size:0.65rem; color:var(--danger);" onclick="deleteActiveSignal(${s.id})">✕</button>
                    </td>
                `;
                tbody.appendChild(row);
            });
        });
    } catch (e) {
        console.error("Failed to refresh active signals", e);
    }
}

window.closeActiveSignal = async (id) => {
    try {
        await api.closeActiveSignal(id);
        showToast('Signal closed', 'info');
        refreshActiveSignals();
    } catch (e) {
        showToast('Failed to close signal', 'error');
    }
};

window.deleteActiveSignal = async (id) => {
    try {
        await api.deleteActiveSignal(id);
        showToast('Signal deleted', 'info');
        refreshActiveSignals();
    } catch (e) {
        showToast('Failed to delete signal', 'error');
    }
};



// Window globals for legacy onclick handlers
window.selectInstrument = selectInstrument;
window.switchBottomTab = (tabId) => switchTab('bottom-panel', `tab-${tabId}`);
window.setChartTimeframe = (interval) => {
    currentInterval = interval;
    localStorage.setItem("currentInterval", interval);

    // Update timeframe buttons exactly by ID
    const allIntervals = ['1minute', '5minute', '15minute', '30minute', '1hour', 'day'];

    allIntervals.forEach(t => {
        const btn = document.getElementById(`tf-${t}`);
        if (btn) {
            if (t === interval) {
                btn.classList.remove('btn-outline');
                btn.classList.add('btn-primary');
            } else {
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-outline');
            }
        }
    });

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

window.saveRiskConfig = async () => {
    const capital = document.getElementById('risk-capital').value;
    const risk = document.getElementById('risk-pct').value;
    const maxLoss = document.getElementById('risk-maxloss').value;
    const maxTrades = document.getElementById('risk-maxtrades').value;
    const side = document.getElementById('setting-trading-side').value;

    const payload = {
        trading_capital: parseFloat(capital),
        risk_per_trade_pct: parseFloat(risk),
        max_daily_loss_pct: parseFloat(maxLoss),
        max_open_trades: parseInt(maxTrades),
        trading_side: side
    };

    try {
        await api.updateConfig(payload);
        showToast("Risk Configuration Saved", "success");
        refreshStatus();
    } catch (e) {
        showToast("Failed to save risk config", "error");
    }
};

window.toggleSandboxMode = async (enabled) => {
    try {
        await api.updateConfig({ use_sandbox: enabled });
        showToast(`Sandbox Mode ${enabled ? 'Enabled' : 'Disabled'}`, 'info');
        refreshStatus();
    } catch (e) {
        showToast("Failed to toggle Sandbox Mode", "error");
    }
};

window.togglePaperMode = async (enabled) => {
    try {
        await api.updateConfig({ paper_trading: enabled });
        showToast(`Paper Trading ${enabled ? 'Enabled' : 'Disabled'}`, 'info');
        refreshStatus();
    } catch (e) {
        showToast("Failed to toggle Paper Mode", "error");
    }
};

window.updateTradingSide = async (side) => {
    try {
        await api.updateConfig({ trading_side: side });
        showToast(`Trading Side updated to ${side}`, 'info');
        refreshStatus();
    } catch (e) {
        showToast("Failed to update Trading Side", "error");
    }
};

window.saveNotificationSettings = async () => {
    const channels = document.getElementById('setting-notification-channels').value;
    const server = document.getElementById('setting-smtp-server').value;
    const port = document.getElementById('setting-smtp-port').value;
    const user = document.getElementById('setting-smtp-user').value;
    const password = document.getElementById('setting-smtp-password').value;
    const recipient = document.getElementById('setting-email-recipient').value;

    const payload = {};
    if (channels) payload.NOTIFICATION_CHANNELS = channels;
    if (server) payload.SMTP_SERVER = server;
    if (port) payload.SMTP_PORT = parseInt(port);
    if (user) payload.SMTP_USER = user;
    if (password && !password.includes('***')) payload.SMTP_PASSWORD = password;
    if (recipient) payload.EMAIL_RECIPIENT = recipient;

    try {
        await api.saveSettings(payload);
        showToast("Notification settings saved", "success");
    } catch (e) {
        showToast("Failed to save notification settings", "error");
    }
};

window.testNotification = async (channel = "email") => {
    try {
        showToast(`Sending test ${channel}...`, "info");
        const res = await api.testNotification(channel);
        if (res.status === 'success') {
            showToast(res.message, "success");
        } else {
            showToast(res.message, "error");
        }
    } catch (e) {
        showToast("Failed to dispatch test notification", "error");
    }
};

async function loadSettingsIntoUI() {
    try {
        const settings = await api.getSettings();

        // General
        if (document.getElementById('setting-api-key')) document.getElementById('setting-api-key').value = settings.API_KEY || '';
        if (document.getElementById('setting-redirect-uri')) document.getElementById('setting-redirect-uri').value = settings.REDIRECT_URI || '';

        // Sandbox & Modes
        if (document.getElementById('setting-sandbox-key')) document.getElementById('setting-sandbox-key').value = settings.SANDBOX_API_KEY || '';
        if (document.getElementById('toggle-sandboxmode')) document.getElementById('toggle-sandboxmode').checked = settings.USE_SANDBOX || false;
        if (document.getElementById('toggle-papermode')) document.getElementById('toggle-papermode').checked = settings.PAPER_TRADING ?? true;

        // Risk
        if (document.getElementById('risk-capital')) document.getElementById('risk-capital').value = settings.TRADING_CAPITAL || 100000;
        if (document.getElementById('risk-pct')) document.getElementById('risk-pct').value = settings.MAX_RISK_PER_TRADE_PCT || 1.0;
        if (document.getElementById('risk-maxloss')) document.getElementById('risk-maxloss').value = settings.MAX_DAILY_LOSS_PCT || 3.0;
        if (document.getElementById('risk-maxtrades')) document.getElementById('risk-maxtrades').value = settings.MAX_OPEN_TRADES || 3;
        if (document.getElementById('setting-trading-side')) document.getElementById('setting-trading-side').value = settings.TRADING_SIDE || 'BOTH';

        // Notifications
        if (document.getElementById('setting-notification-channels')) document.getElementById('setting-notification-channels').value = settings.NOTIFICATION_CHANNELS || 'EMAIL';
        if (document.getElementById('setting-smtp-server')) document.getElementById('setting-smtp-server').value = settings.SMTP_SERVER || '';
        if (document.getElementById('setting-smtp-port')) document.getElementById('setting-smtp-port').value = settings.SMTP_PORT || 587;
        if (document.getElementById('setting-smtp-user')) document.getElementById('setting-smtp-user').value = settings.SMTP_USER || '';
        if (document.getElementById('setting-smtp-password')) document.getElementById('setting-smtp-password').value = settings.SMTP_PASSWORD || '';
        if (document.getElementById('setting-email-recipient')) document.getElementById('setting-email-recipient').value = settings.EMAIL_RECIPIENT || '';

    } catch (e) {
        console.error("Failed to load settings into UI", e);
    }
}

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
        const [data, status] = await Promise.all([
            api.getStrategySchemas(),
            api.getStatus()
        ]);

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

        // Select matching strategy from engine status or default to index 0
        const targetClass = status.active_strategy_class;
        let selectedIdx = 0;

        if (targetClass) {
            for (let i = 0; i < selector.options.length; i++) {
                if (selector.options[i].dataset.class === targetClass) {
                    selectedIdx = i;
                    break;
                }
            }
        }

        if (selector.options.length > 0) {
            selector.selectedIndex = selectedIdx;
            window.renderDynamicStrategyForm();
            refreshOverlay();
        }
    } catch(e) {
        console.error("Failed to load strategy schemas", e);
    }
}

window.renderDynamicStrategyForm = () => {
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
                const primarySeries = res.overlay.filter(pt => pt.supertrend !== null).map(pt => ({
                    time: Math.floor(pt.time),
                    value: pt.supertrend,
                    color: pt.trend === 1 ? '#00d084' : '#ff4757'
                }));

                // Secondary series (e.g. Slow EMA for ScalpPro)
                const secondarySeries = res.overlay.filter(pt => pt.upper !== null).map(pt => ({
                    time: Math.floor(pt.time),
                    value: pt.upper,
                    color: '#FF9800' // Distinct color for secondary line
                }));

                const markers = [];
                let lastTrend = null;
                res.overlay.forEach(pt => {
                    const ds = pt.time;
                    if (lastTrend !== null && pt.trend !== lastTrend) {
                        const isVerifiedSignal = pt.signal === 'BUY' || pt.signal === 'SELL';
                        markers.push({
                            time: Math.floor(ds),
                            position: pt.trend === 1 ? 'belowBar' : 'aboveBar',
                            color: pt.trend === 1 ? (isVerifiedSignal ? '#00ffaa' : 'rgba(0, 208, 132, 0.4)') : (isVerifiedSignal ? '#ff3366' : 'rgba(255, 71, 87, 0.4)'),
                            shape: pt.trend === 1 ? 'arrowUp' : 'arrowDown',
                            text: isVerifiedSignal ? pt.signal : 'ST Flip'
                        });
                    }
                    lastTrend = pt.trend;
                });
                chart.setOverlayData(primarySeries, secondarySeries);
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

    // Catch-all for any other metrics (Generic support for new strategies like ScalpPro)
    const handledKeys = ['tf_profile', 'tf_mode', 'exit_mode', 'trend', 'hard_gates', 'soft_filters', 'bars_in_trend'];
    Object.keys(m).forEach(key => {
        if (!handledKeys.includes(key)) {
            addRow(key, m[key], bgRow, bgRow);
        }
    });

    html += `</div>`;
    document.getElementById("strategy-hud-container").innerHTML = html;
}
window.switchMainView = (view) => {
    // Hide all
    const views = ['tvchart', 'option-chain-container', 'watchlist-view-container', 'settings-view-container'];
    views.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });

    // Update button states
    const btnMap = {
        'chart': 'btn-view-chart',
        'options': 'btn-view-options',
        'watchlist': 'btn-view-watchlist',
        'settings': 'btn-view-settings-center'
    };
    Object.values(btnMap).forEach(bid => {
        document.getElementById(bid)?.classList.replace('btn-primary', 'btn-outline');
    });

    // Show target
    const targetMap = {
        'chart': 'tvchart',
        'options': 'option-chain-container',
        'watchlist': 'watchlist-view-container',
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

    // Refresh watchlist + active signals when switching to watchlist view
    if (view === 'watchlist') {
        refreshWatchlist();
        refreshActiveSignals();
    }

    // Persist instrument name across view transitions
    updateElementText('current-instrument', currentInstrumentName);
    updateElementText('oc-instrument-name', currentInstrumentName);

    if (view === 'options') fetchOptionChain();
    if (view === 'settings') {
        refreshStatus();
        loadSettingsIntoUI();
    }
};

function switchTab(containerId, contentId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Manage dynamic subscriptions for bottom panel
    if (containerId === 'bottom-panel') {
        const isLeavingPositions = document.querySelector('.bottom-tab.active')?.innerText.toLowerCase().includes('position');
        const isEnteringPositions = contentId === 'tab-positions';

        if (isLeavingPositions && !isEnteringPositions) {
            unsubscribeFromPositions();
        } else if (isEnteringPositions) {
            subscribeToPositions();
        }

        // Option Chain logic
        const prevTabName = document.querySelector('.bottom-tab.active')?.innerText.toLowerCase() || "";
        const isLeavingOptions = prevTabName.includes('option chain');
        const isEnteringOptions = contentId === 'tab-options';

        if (isLeavingOptions && !isEnteringOptions) {
            unsubscribeFromOptionChain();
        }
    }

    // 1. Reset buttons
    container.querySelectorAll('.bottom-tab, .settings-tab, .sidebar-link').forEach(btn => {
        btn.classList.remove('active');
    });

    // 2. Hide all content containers within this scope
    // Use more specific selectors if needed, but for now we hide everything in the container
    const allContents = container.querySelectorAll('.bottom-content, .settings-content, .tab-content, .settings-view-container');
    allContents.forEach(content => {
        content.classList.remove('active');
        // Ensure display:none is forced if class removal isn't enough
        if (content.classList.contains('bottom-content') || content.classList.contains('settings-content')) {
            content.style.display = 'none';
        }
    });

    // 3. Activate the clicked tab button
    // Find button that contains the contentId in its onclick
    const clickedButton = container.querySelector(`[onclick*="${contentId.replace('tab-', '')}"]`);
    if (clickedButton) {
        clickedButton.classList.add('active');
    }

    // 4. Activate the corresponding content
    const targetContent = document.getElementById(contentId);
    if (targetContent) {
        targetContent.classList.add('active');
        // Force display:block for visibility
        if (targetContent.classList.contains('bottom-content') || targetContent.classList.contains('settings-content')) {
            targetContent.style.display = 'block';
        }
    }
}

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

            // Subscribe to visible strikes (±10 around ATM)
            if (ws && ws.isConnected()) {
                const keys = [];
                res.chain.forEach(row => {
                    if (row.ce?.instrument_key) keys.push(row.ce.instrument_key);
                    if (row.pe?.instrument_key) keys.push(row.pe.instrument_key);
                });
                if (keys.length > 0) {
                    ws.send({ action: 'subscribe', instrument_key: keys.join(',') });
                }
            }
        }
    } catch (e) {
        showToast("Failed to fetch option chain", "error");
    }
};

function unsubscribeFromOptionChain() {
    if (!ws || !ws.isConnected()) return;
    const cells = document.querySelectorAll('#oc-tbody [data-key]');
    const keys = new Set();
    cells.forEach(c => {
        const k = c.dataset.key;
        if (k && shouldUnsubscribe(k)) keys.add(k);
    });
    if (keys.size > 0) {
        ws.send({ action: 'unsubscribe', instrument_key: Array.from(keys).join(',') });
    }
}

function renderOptionChain(data) {
    const list = document.getElementById('oc-tbody');
    if (!list) return;

    // Clear only OC-related cache
    for (const key of domNodes.keys()) {
        if (key.startsWith('oc-')) domNodes.delete(key);
    }

    list.innerHTML = "";

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
            <td data-key="${ce.instrument_key}" data-field="delta" style="color:var(--text-muted); font-size:0.7rem;">${(ce.delta || 0).toFixed(2)}</td>
            <td data-key="${ce.instrument_key}" data-field="theta" style="color:var(--text-muted); font-size:0.7rem;">${(ce.theta || 0).toFixed(2)}</td>
            <td data-key="${ce.instrument_key}" data-field="iv" style="color:var(--text-muted);">${(ce.iv || 0).toFixed(1)}%</td>
            <td style="width: 60px;">
                <div data-key="${ce.instrument_key}" data-field="volume" style="font-size:0.65rem; color: #8b8b9e;">${(ce.volume || 0).toLocaleString()}</div>
                <div style="height:2px; background:#00d084; width:${Math.min(100, (ce.volume || 0)/1000)}%; opacity:0.5;"></div>
            </td>
            <td data-key="${ce.instrument_key}" data-field="ltp" style="font-weight:600; color:#10b981; background:${ceITM ? 'rgba(16,185,129,0.08)' : 'transparent'}">${ce.ltp ? formatPrice(ce.ltp) : '-'}</td>
            <td style="background:var(--bg-dark); font-weight:700; border-left:1px solid var(--border-color); border-right:1px solid var(--border-color);">${row.strike_price}</td>
            <td data-key="${pe.instrument_key}" data-field="ltp" style="font-weight:600; color:#ef4444; background:${peITM ? 'rgba(239,68,68,0.08)' : 'transparent'}">${pe.ltp ? formatPrice(pe.ltp) : '-'}</td>
            <td style="width: 60px;">
                <div data-key="${pe.instrument_key}" data-field="volume" style="font-size:0.65rem; color: #8b8b9e;">${(pe.volume || 0).toLocaleString()}</div>
                <div style="height:2px; background:#ef4444; width:${Math.min(100, (pe.volume || 0)/1000)}%; opacity:0.5;"></div>
            </td>
            <td data-key="${pe.instrument_key}" data-field="iv" style="color:var(--text-muted);">${(pe.iv || 0).toFixed(1)}%</td>
            <td data-key="${pe.instrument_key}" data-field="theta" style="color:var(--text-muted); font-size:0.7rem;">${(pe.theta || 0).toFixed(2)}</td>
            <td data-key="${pe.instrument_key}" data-field="delta" style="color:var(--text-muted); font-size:0.7rem;">${(pe.delta || 0).toFixed(2)}</td>
        `;
        list.appendChild(tr);

        // Cache references for fast WS updates
        if (ce.instrument_key) {
            domNodes.set(`oc-${ce.instrument_key}`, {
                ltp: tr.querySelector(`[data-key="${ce.instrument_key}"][data-field="ltp"]`),
                volume: tr.querySelector(`[data-key="${ce.instrument_key}"][data-field="volume"]`),
                iv: tr.querySelector(`[data-key="${ce.instrument_key}"][data-field="iv"]`),
                delta: tr.querySelector(`[data-key="${ce.instrument_key}"][data-field="delta"]`),
                theta: tr.querySelector(`[data-key="${ce.instrument_key}"][data-field="theta"]`)
            });
        }
        if (pe.instrument_key) {
            domNodes.set(`oc-${pe.instrument_key}`, {
                ltp: tr.querySelector(`[data-key="${pe.instrument_key}"][data-field="ltp"]`),
                volume: tr.querySelector(`[data-key="${pe.instrument_key}"][data-field="volume"]`),
                iv: tr.querySelector(`[data-key="${pe.instrument_key}"][data-field="iv"]`),
                delta: tr.querySelector(`[data-key="${pe.instrument_key}"][data-field="delta"]`),
                theta: tr.querySelector(`[data-key="${pe.instrument_key}"][data-field="theta"]`)
            });
        }
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
            selectInstrument(inst.instrument_key, inst.name);
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
