/**
 * state.js — Centralised application state with pub/sub.
 *
 * Usage:
 *   import { state } from './state.js';
 *
 *   // Read
 *   state.get('currentInstrumentKey');
 *
 *   // Write (fires listeners)
 *   state.set('currentInstrumentKey', 'NSE_INDEX|Nifty 50');
 *
 *   // Batch (fires one notification per key)
 *   state.batch({ currentInstrumentKey: '...', currentInterval: '15m' });
 *
 *   // Subscribe to changes
 *   const unsub = state.on('currentInstrumentKey', (value, prev) => { ... });
 *   unsub(); // stop listening
 *
 *   // Subscribe to any change
 *   state.onAny((key, value, prev) => { ... });
 */

const _store = {
    // Persisted (survives page refresh via localStorage)
    currentInstrumentKey: localStorage.getItem('currentInstrumentKey') || 'NSE_INDEX|Nifty 50',
    currentInstrumentName: localStorage.getItem('currentInstrumentName') || 'Nifty 50',
    currentInterval: localStorage.getItem('currentInterval') || '15minute',

    // Session-scoped (reset on page load)
    engineActive: false,
    currentMainView: 'chart',
    overlayRefreshInFlight: false,
    lastOverlayRefreshAt: 0,
};

// Keys that are persisted to localStorage on change
const _persistKeys = new Set([
    'currentInstrumentKey',
    'currentInstrumentName',
    'currentInterval',
]);

/** @type {Map<string, Set<(value: any, prev: any) => void>>} */
const _listeners = new Map();

/** @type {Set<(key: string, value: any, prev: any) => void>} */
const _anyListeners = new Set();


export const state = {
    /**
     * Get a state value.
     * @param {string} key
     */
    get(key) {
        return _store[key];
    },

    /**
     * Set a state value and notify listeners.
     * @param {string} key
     * @param {*} value
     */
    set(key, value) {
        const prev = _store[key];
        if (prev === value) return;
        _store[key] = value;
        if (_persistKeys.has(key)) {
            try { localStorage.setItem(key, value); } catch { /* quota */ }
        }
        _notify(key, value, prev);
    },

    /**
     * Set multiple keys at once.
     * @param {Record<string, *>} updates
     */
    batch(updates) {
        for (const [key, value] of Object.entries(updates)) {
            this.set(key, value);
        }
    },

    /**
     * Subscribe to changes on a specific key.
     * Returns an unsubscribe function.
     * @param {string} key
     * @param {(value: any, prev: any) => void} fn
     * @returns {() => void}
     */
    on(key, fn) {
        if (!_listeners.has(key)) _listeners.set(key, new Set());
        _listeners.get(key).add(fn);
        return () => _listeners.get(key)?.delete(fn);
    },

    /**
     * Subscribe to all state changes.
     * @param {(key: string, value: any, prev: any) => void} fn
     * @returns {() => void}
     */
    onAny(fn) {
        _anyListeners.add(fn);
        return () => _anyListeners.delete(fn);
    },
};


function _notify(key, value, prev) {
    const fns = _listeners.get(key);
    if (fns) {
        for (const fn of fns) {
            try { fn(value, prev); } catch (e) { console.error(`state[${key}] listener error:`, e); }
        }
    }
    for (const fn of _anyListeners) {
        try { fn(key, value, prev); } catch (e) { console.error('state.onAny listener error:', e); }
    }
}
