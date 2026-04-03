/**
 * ui.js — DOM helpers and visual feedback.
 */

export function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerText = message;

    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        document.body.appendChild(container);
    }

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}

export function formatPrice(p) {
    return Number(p).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function updateElementText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

function ensurePanelStylesInjected() {
    if (document.getElementById('hud-oc-inline-fallback')) return;
    const style = document.createElement('style');
    style.id = 'hud-oc-inline-fallback';
    style.textContent = `
        .hud-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#404040;font-family:'JetBrains Mono','Fira Code',monospace;font-size:.75rem}
        .hud-cell{padding:4px 8px}
        .hud-cell-header{background:#1e222d;color:#fff}
        .hud-cell-row{background:#2a2e39;color:#fff}
        .hud-cell-info{background:rgba(0,188,212,.2);color:#00bcd4}
        .hud-cell-accent{background:rgba(255,152,0,.2);color:#ff9800}
        .hud-cell-pass{background:rgba(76,175,80,.2);color:#4caf50}
        .hud-cell-fail{background:rgba(244,67,54,.2);color:#ff5252}
        .hud-cell-warn{background:rgba(255,235,59,.2);color:#ffeb3b}
        .oc-panel-head{padding:8px}
        .oc-panel-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
        .oc-sentiment{font-size:.8rem;font-weight:600}
        .oc-score-label{font-size:.7rem;color:var(--text-muted,#8b8b9e)}
        .oc-expiry{font-size:.65rem;color:var(--text-muted,#8b8b9e);margin-bottom:4px}
        .oc-bar-track{height:4px;background:#404040;border-radius:2px;margin-bottom:8px;position:relative}
        .oc-bar-center{position:absolute;left:50%;top:0;width:1px;height:100%;background:#666}
        .oc-bar-fill{height:100%;border-radius:2px}
        .oc-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#404040;font-family:'JetBrains Mono','Fira Code',monospace;font-size:.7rem}
        .oc-label{padding:3px 6px;background:#1e222d;color:#fff}
        .oc-value{padding:3px 6px;background:#2a2e39;color:#fff}
        .oc-signals{padding:6px 8px;font-size:.65rem;color:var(--text-muted,#8b8b9e);border-top:1px solid #404040;max-height:80px;overflow-y:auto}
        .oc-signal-row{margin-bottom:2px}
        .oc-tone-bull{color:#4caf50}.oc-tone-bear{color:#ff5252}.oc-tone-neutral{color:#ff9800}
        .oc-bg-bull{background:rgba(76,175,80,.15)}.oc-bg-bear{background:rgba(244,67,54,.15)}
    `;
    document.head.appendChild(style);
}

export function renderStrategyHUDEmpty(message) {
    ensurePanelStylesInjected();
    const hud = document.getElementById("strategy-hud-container");
    if (!hud) return;
    hud.innerHTML = `<div class="hud-empty">
        <div class="hud-empty-title">📊 SuperTrend Pro v6.3</div>
        <div class="hud-empty-message">${message}</div>
    </div>`;
}

export function renderStrategyHUD(strategy) {
    ensurePanelStylesInjected();
    const m = strategy.latest_metrics;
    if (!m) {
        renderStrategyHUDEmpty("No metrics data available.");
        return;
    }

    const passCol = (ok) => ok ? "hud-cell-pass" : "hud-cell-fail";

    let html = `<div class="hud-grid">`;

    const addRow = (label, val, labelClass, valClass) => {
        html += `<div class="hud-cell ${labelClass}">${label}</div>`;
        html += `<div class="hud-cell ${valClass}">${val}</div>`;
    };

    addRow("Metric", "Value", "hud-cell-header", "hud-cell-header");

    if (m.tf_profile) addRow("TF profile", `${m.tf_profile} (${m.tf_mode || ''})`, "hud-cell-info", "hud-cell-info");
    if (m.exit_mode) addRow("Exit mode", m.exit_mode, "hud-cell-info", "hud-cell-info");
    if (m.trend) addRow("ST Trend", m.trend, "hud-cell-row", m.trend === "LONG" ? "hud-cell-pass" : "hud-cell-fail");

    if (m.hard_gates) {
        if (m.hard_gates.dual_st) addRow("H1 Dual ST", m.hard_gates.dual_st, "hud-cell-accent", m.hard_gates.dual_st === "AGREE" ? "hud-cell-pass" : "hud-cell-fail");
        if (m.hard_gates.consecutive) {
            const parts = m.hard_gates.consecutive.split('/');
            const consecOk = parseInt(parts[0]) >= parseInt(parts[1]);
            addRow("H2 Consec", `${m.hard_gates.consecutive} ${consecOk ? 'PASS' : 'FAIL'}`, "hud-cell-accent", passCol(consecOk));
        }
    }

    if (m.soft_filters) {
        if (m.soft_filters.score !== undefined) addRow("Soft score", m.soft_filters.score, "hud-cell-row", "hud-cell-warn");
        if (m.soft_filters.adx) addRow("S1 ADX", `${m.soft_filters.adx.value} ${m.soft_filters.adx.pass ? 'PASS' : 'FAIL'}`, "hud-cell-row", passCol(m.soft_filters.adx.pass));
        if (m.soft_filters.volume) addRow("S2 Volume", m.soft_filters.volume.pass ? "SURGE" : "FLAT", "hud-cell-row", passCol(m.soft_filters.volume.pass));
        if (m.soft_filters.atr_pct) addRow("S3 ATR%", `${m.soft_filters.atr_pct.value}%`, "hud-cell-row", passCol(m.soft_filters.atr_pct.pass));
        if (m.soft_filters.roc) addRow("S4 ROC", `${m.soft_filters.roc.value}%`, "hud-cell-row", passCol(m.soft_filters.roc.pass));
        if (m.soft_filters.bb_squeeze) {
            const bb = m.soft_filters.bb_squeeze;
            addRow("S5 BB", bb.state, "hud-cell-row", bb.pass ? "hud-cell-pass" : (bb.state === 'SQUEEZE' ? "hud-cell-warn" : "hud-cell-fail"));
        }
    }

    if (m.bars_in_trend !== undefined) addRow("Bars held", m.bars_in_trend, "hud-cell-row", "hud-cell-row");

    const handledKeys = ['tf_profile', 'tf_mode', 'exit_mode', 'trend', 'hard_gates', 'soft_filters', 'bars_in_trend'];
    Object.keys(m).forEach(key => {
        if (!handledKeys.includes(key)) {
            addRow(key, m[key], "hud-cell-row", "hud-cell-row");
        }
    });

    html += `</div>`;
    document.getElementById("strategy-hud-container").innerHTML = html;
}

export function renderOcInsight(a, expiry) {
    ensurePanelStylesInjected();
    const container = document.getElementById("oc-insight-container");
    if (!container || !a) return;

    const ds = a.directional_score || 0;
    const sentiment = a.sentiment || "NEUTRAL";
    const barPct = Math.abs(ds);
    const barColor = ds >= 0 ? "#4caf50" : "#ff5252";

    const pcr = a.pcr || {};
    const mp = a.max_pain || {};
    const oi = a.oi_concentration || {};
    const iv = a.iv_skew || {};
    const oib = a.oi_buildup || {};
    const vv = a.veteran_view || {};

    const biasClass = (b) => b === "BULLISH" ? "oc-tone-bull" : b === "BEARISH" ? "oc-tone-bear" : "oc-tone-neutral";

    let html = `
    <div class="oc-panel-head">
        <div class="oc-panel-row">
            <span class="oc-sentiment ${biasClass(sentiment)}">${sentiment}</span>
            <span class="oc-score-label">Score: <b class="${biasClass(sentiment)}">${ds > 0 ? '+' : ''}${ds}</b></span>
        </div>
        ${expiry ? `<div class="oc-expiry">Expiry: ${expiry}</div>` : ''}
        <div class="oc-bar-track">
            <div class="oc-bar-center"></div>
            <div class="oc-bar-fill" style="width: ${barPct}%; background: ${barColor}; margin-left: ${ds >= 0 ? '50%' : (50 - barPct) + '%'};"></div>
        </div>
    </div>`;

    html += `<div class="oc-grid">`;

    const addRow = (label, val, valClass = "oc-value") => {
        html += `<div class="oc-label">${label}</div>`;
        html += `<div class="oc-value ${valClass}">${val}</div>`;
    };

    const pcrVal = (pcr.pcr_oi || 0).toFixed(2);
    const pcrClass = pcr.pcr_oi > 1.0 ? "oc-bg-bull oc-tone-bull" : pcr.pcr_oi < 0.8 ? "oc-bg-bear oc-tone-bear" : "";
    addRow("PCR (OI)", pcrVal, pcrClass);

    addRow("Max Pain", mp.max_pain_strike ? mp.max_pain_strike.toLocaleString() : '-');
    if (a.spot_price) addRow("Spot", a.spot_price.toLocaleString());
    if (oi.immediate_support) addRow("Support", oi.immediate_support.toLocaleString(), "oc-bg-bull oc-tone-bull");
    if (oi.immediate_resistance) addRow("Resistance", oi.immediate_resistance.toLocaleString(), "oc-bg-bear oc-tone-bear");

    const skewVal = `${iv.skew_bias || 'N/A'} (${(iv.iv_skew || 0).toFixed(1)})`;
    addRow("IV Skew", skewVal, biasClass(iv.skew_bias));
    addRow("OI Bias", oib.oi_bias || 'N/A', biasClass(oib.oi_bias));

    html += `</div>`;

    if (a.signals && a.signals.length > 0) {
        html += `<div class="oc-signals">`;
        a.signals.forEach(s => {
            const icon = s.includes('BULLISH') || s.includes('bullish') || s.includes('support') ? '🟢' : s.includes('BEARISH') || s.includes('bearish') || s.includes('resistance') ? '🔴' : '⚪';
            html += `<div class="oc-signal-row">${icon} ${s}</div>`;
        });
        html += `</div>`;
    }

    if (vv && (vv.setup || vv.invalidation || vv.market_regime)) {
        html += `<div class="oc-signals" style="border-top:1px dashed #5a5a5a; margin-top:2px;">
            <div class="oc-signal-row"><b>Veteran View:</b> ${vv.market_regime || 'RANGE'}${Number.isFinite(vv.confidence) ? ` | Confidence ${vv.confidence}%` : ''}</div>
            ${vv.setup ? `<div class="oc-signal-row">🎯 Setup: ${vv.setup}</div>` : ''}
            ${vv.invalidation ? `<div class="oc-signal-row">🛑 Invalidation: ${vv.invalidation}</div>` : ''}
            ${vv.execution_note ? `<div class="oc-signal-row">🧭 ${vv.execution_note}</div>` : ''}
        </div>`;
    }

    container.innerHTML = html;
}
