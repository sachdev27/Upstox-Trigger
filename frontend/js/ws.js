/**
 * ws.js — WebSocket management and event handling.
 */

import { showToast } from './ui.js';

const WS_URL = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws";

export class EngineWS {
    constructor(onMessage) {
        this.onMessage = onMessage;
        this.ws = null;
        this.reconnectInterval = 3000;
    }

    connect() {
        const wsStatusText = document.getElementById("ws-status-text");
        const wsStatusBadge = document.getElementById("ws-status");

        this.ws = new WebSocket(WS_URL);

        this.ws.onopen = () => {
            if (wsStatusText) wsStatusText.innerText = "Live";
            if (wsStatusBadge) wsStatusBadge.className = "status-badge online";
            showToast("Connected to Engine WebSocket");
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (this.onMessage) this.onMessage(msg);
            } catch (e) {
                console.error("WS Parse Error:", e);
            }
        };

        this.ws.onclose = () => {
            if (wsStatusText) wsStatusText.innerText = "Reconnecting...";
            if (wsStatusBadge) wsStatusBadge.className = "status-badge offline";
            setTimeout(() => this.connect(), this.reconnectInterval);
        };

        this.ws.onerror = (err) => {
            console.error("WS Error:", err);
            this.ws.close();
        };
    }

    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }
}
