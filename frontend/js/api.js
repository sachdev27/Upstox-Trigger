/**
 * api.js — Centralized API client for the frontend.
 */

const API_BASE = window.location.origin;

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

    // Orders
    async getTrades() {
        return fetch(`${API_BASE}/orders/trades`).then(r => r.json());
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
    async setAutoMode(enabled) {
        return fetch(`${API_BASE}/engine/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auto_mode: enabled })
        }).then(r => r.json());
    },
    async getPositions() {
        return fetch(`${API_BASE}/orders/positions`).then(r => r.json());
    },
    async getSignals() {
        return fetch(`${API_BASE}/strategies/signals`).then(r => r.json());
    },
    async triggerTestSignal(instrumentKey) {
        return fetch(`${API_BASE}/engine/test-signal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instrument_key: instrumentKey })
        }).then(r => r.json());
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
    }
};
