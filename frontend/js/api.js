/**
 * api.js — Centralized API client for the frontend.
 */

const API_BASE = window.location.origin;

async function fetchJsonWithRetry(url, options = {}, retries = 2, retryDelayMs = 250) {
    let lastError = null;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
        try {
            const res = await fetch(url, options);
            return await res.json();
        } catch (err) {
            lastError = err;
            if (attempt < retries) {
                await new Promise((resolve) => setTimeout(resolve, retryDelayMs * (attempt + 1)));
            }
        }
    }
    throw lastError;
}

export async function fetchWithToast(url, options = {}, successMsg = null) {
    try {
        const res = await fetch(url, options);
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || "Request failed");
        if (successMsg) showToast(successMsg, "success");
        return data;
    } catch (e) {
        showToast(e.message, "error");
        throw e;
    }
}

export const api = {
    // Market Data
    async searchInstruments(query) {
        return fetch(`${API_BASE}/market/instruments/search?query=${encodeURIComponent(query)}`).then(r => r.json());
    },
    async getFeaturedInstruments() {
        return fetch(`${API_BASE}/market/instruments/featured`).then(r => r.json());
    },
    async getHistoricalCandles(instrumentKey, interval) {
        return fetch(`${API_BASE}/market/candles?instrument_key=${instrumentKey}&interval=${interval}`).then(r => r.json());
    },
    async getOptionChain(instrumentKey, expiry = null) {
        let url = `${API_BASE}/market/option-chain?instrument_key=${instrumentKey}`;
        if (expiry) url += `&expiry_date=${expiry}`;
        return fetch(url).then(r => r.json());
    },
    async getOptionChainAnalysis(instrumentKey) {
        return fetch(`${API_BASE}/market/option-chain/analysis?instrument_key=${encodeURIComponent(instrumentKey)}`).then(r => r.json());
    },

    // Orders
    async getTrades() {
        return fetch(`${API_BASE}/orders/trades`).then(r => r.json());
    },
    async getPaperTrades(limit = 100) {
        return fetch(`${API_BASE}/orders/trades/paper?limit=${limit}`).then(r => r.json());
    },
    async getOrderBook() {
        return fetch(`${API_BASE}/orders/book`).then(r => r.json());
    },
    async placeOrder(payload) {
        const params = new URLSearchParams(payload);
        return fetch(`${API_BASE}/orders/place?${params.toString()}`, { method: 'POST' }).then(r => r.json());
    },
    async getFunds() {
        return fetch(`${API_BASE}/orders/funds`).then(r => r.json());
    },
    async getHoldings() {
        return fetch(`${API_BASE}/orders/holdings`).then(r => r.json());
    },

    // Engine
    async getStatus() {
        return fetch(`${API_BASE}/engine/status`).then(r => r.json());
    },
    async initializeEngine() {
        return fetch(`${API_BASE}/engine/initialize`, { method: 'POST' }).then(r => r.json());
    },
    async runCycle() {
        return fetch(`${API_BASE}/engine/run-cycle`, { method: 'POST' }).then(r => r.json());
    },
    async updateConfig(config) {
        return fetch(`${API_BASE}/engine/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        }).then(r => r.json());
    },
    async getPositions() {
        return fetch(`${API_BASE}/orders/positions`).then(r => r.json());
    },
    async getSignals() {
        return fetch(`${API_BASE}/strategies/signals`).then(r => r.json());
    },
    async getMarketStatus() {
        return fetch(`${API_BASE}/orders/status/market-hours`).then(r => r.json());
    },
    async triggerTestSignal(instrumentKey, action = 'BUY', forceLive = false) {
        return fetch(`${API_BASE}/engine/test-signal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instrument_key: instrumentKey, action, force_live: forceLive })
        }).then(r => r.json());
    },
    async setAutoMode(enabled) {
        return fetch(`${API_BASE}/engine/auto-mode?enabled=${enabled}`, { method: 'POST' }).then(r => r.json());
    },
    async loadStrategy(payload) {
        const params = new URLSearchParams(payload);
        return fetch(`${API_BASE}/engine/load-strategy?${params.toString()}`, { method: 'POST' }).then(r => r.json());
    },
    async getStrategySchemas() {
        return fetch(`${API_BASE}/strategies/schema`).then(r => r.json());
    },
    async getStrategyOverlay(instrumentKey, interval, strategyClass, params) {
        return fetch(`${API_BASE}/market/strategy-overlay?instrument_key=${instrumentKey}&timeframe=${interval}&strategy_class=${strategyClass}&params=${encodeURIComponent(JSON.stringify(params))}`).then(r => r.json());
    },

    // Settings
    async getSettings() {
        return fetch(`${API_BASE}/settings/`).then(r => r.json());
    },
    async saveSettings(settings) {
        return fetch(`${API_BASE}/settings/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        }).then(r => r.json());
    },
    async testNotification(channel = "email") {
        return fetch(`${API_BASE}/notifications/test?channel=${channel}`, {
            method: 'POST'
        }).then(r => r.json());
    },

    // Watchlist
    async getWatchlist() {
        return fetch(`${API_BASE}/monitoring/watchlist`).then(r => r.json());
    },
    async addToWatchlist(instrumentKey, timeframes = "15m") {
        return fetch(`${API_BASE}/monitoring/watchlist?instrument_key=${encodeURIComponent(instrumentKey)}&timeframes=${encodeURIComponent(timeframes)}`, {
            method: 'POST'
        }).then(r => r.json());
    },
    async removeFromWatchlist(instrumentKey) {
        return fetch(`${API_BASE}/monitoring/watchlist/${encodeURIComponent(instrumentKey)}`, {
            method: 'DELETE'
        }).then(r => r.json());
    },
    async updateWatchlistTimeframes(itemId, timeframes) {
        return fetch(`${API_BASE}/monitoring/watchlist/${itemId}/timeframes?timeframes=${encodeURIComponent(timeframes)}`, {
            method: 'PUT'
        }).then(r => r.json());
    },
    async exportWatchlist() {
        return fetch(`${API_BASE}/monitoring/watchlist/export`);
    },
    async importWatchlist(file) {
        const formData = new FormData();
        formData.append('file', file);
        return fetch(`${API_BASE}/monitoring/watchlist/import`, {
            method: 'POST',
            body: formData
        }).then(r => r.json());
    },

    // Active Signals
    async getActiveSignals(status = null) {
        let url = `${API_BASE}/monitoring/active-signals`;
        if (status) url += `?status=${status}`;
        try {
            return await fetchJsonWithRetry(url, {}, 2, 300);
        } catch (e) {
            console.warn('Active signals fetch failed after retries:', e);
            return { status: 'error', data: [] };
        }
    },
    async closeActiveSignal(id) {
        return fetch(`${API_BASE}/monitoring/active-signals/${id}/close`, {
            method: 'POST'
        }).then(r => r.json());
    },
    async deleteActiveSignal(id) {
        return fetch(`${API_BASE}/monitoring/active-signals/${id}`, {
            method: 'DELETE'
        }).then(r => r.json());
    },
};
