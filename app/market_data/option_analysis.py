"""
Option Chain Analysis — real-time OI, PCR, IV, and Max-Pain analytics.

Consumes the option chain matrix returned by MarketDataService.get_detailed_option_chain()
and produces actionable trading insights:

    • PCR (Put-Call Ratio)         — sentiment gauge
    • OI Concentration             — support/resistance from option writers
    • Max Pain                     — strike where option writers profit most
    • IV Skew                      — directional bias from implied volatility
    • OI Change Direction          — where smart money is building positions
    • Strike-Level Signals         — walls, buildup, unwinding

All functions are **pure computation** — no API calls, no side effects.
They take the chain matrix as input and return dicts/numbers.
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Core Analytics ───────────────────────────────────────────────


def compute_pcr(chain: list[dict]) -> dict:
    """
    Put-Call Ratio from total OI and volume.

    Returns:
        {
            "pcr_oi":     float,   # PE OI / CE OI  (>1 = bullish, <0.7 = bearish)
            "pcr_volume": float,   # PE Vol / CE Vol
            "total_ce_oi":  float,
            "total_pe_oi":  float,
            "total_ce_vol": int,
            "total_pe_vol": int,
        }
    """
    ce_oi = pe_oi = 0.0
    ce_vol = pe_vol = 0

    for row in chain:
        ce = row.get("ce")
        pe = row.get("pe")
        if ce:
            ce_oi += float(ce.get("oi") or 0)
            ce_vol += int(ce.get("volume") or 0)
        if pe:
            pe_oi += float(pe.get("oi") or 0)
            pe_vol += int(pe.get("volume") or 0)

    return {
        "pcr_oi": round(pe_oi / ce_oi, 3) if ce_oi > 0 else 0.0,
        "pcr_volume": round(pe_vol / ce_vol, 3) if ce_vol > 0 else 0.0,
        "total_ce_oi": ce_oi,
        "total_pe_oi": pe_oi,
        "total_ce_vol": ce_vol,
        "total_pe_vol": pe_vol,
    }


def compute_max_pain(chain: list[dict]) -> dict:
    """
    Max Pain — the strike price where total option buyer loss is maximum
    (i.e. option writers' profit is maximized).

    Price tends to gravitate towards max-pain near expiry.

    Returns:
        {
            "max_pain_strike": float,
            "loss_at_max_pain": float,
        }
    """
    strikes = []
    for row in chain:
        sp = float(row.get("strike_price", 0))
        ce_oi = float(row["ce"]["oi"]) if row.get("ce") else 0
        pe_oi = float(row["pe"]["oi"]) if row.get("pe") else 0
        strikes.append((sp, ce_oi, pe_oi))

    if not strikes:
        return {"max_pain_strike": 0.0, "loss_at_max_pain": 0.0}

    min_loss = float("inf")
    max_pain_strike = 0.0

    for target_sp, _, _ in strikes:
        total_loss = 0.0
        for sp, ce_oi, pe_oi in strikes:
            # CE buyers lose if price < strike (OTM)
            if target_sp < sp:
                total_loss += 0  # CE expires worthless — no loss to writers
            else:
                total_loss += (target_sp - sp) * ce_oi  # CE ITM loss

            # PE buyers lose if price > strike (OTM)
            if target_sp > sp:
                total_loss += 0  # PE expires worthless
            else:
                total_loss += (sp - target_sp) * pe_oi  # PE ITM loss

        if total_loss < min_loss:
            min_loss = total_loss
            max_pain_strike = target_sp

    return {
        "max_pain_strike": max_pain_strike,
        "loss_at_max_pain": round(min_loss, 2),
    }


def compute_oi_concentration(
    chain: list[dict], spot: float, num_strikes: int = 5
) -> dict:
    """
    Find strikes with highest OI near spot — these act as support/resistance.

    Returns:
        {
            "highest_ce_oi_strike": float,   # biggest CE OI = resistance
            "highest_pe_oi_strike": float,   # biggest PE OI = support
            "ce_oi_walls": [{strike, oi}],   # top-N CE OI strikes
            "pe_oi_walls": [{strike, oi}],   # top-N PE OI strikes
            "immediate_resistance": float,   # nearest CE wall above spot
            "immediate_support":    float,   # nearest PE wall below spot
        }
    """
    ce_strikes = []
    pe_strikes = []

    for row in chain:
        sp = float(row.get("strike_price", 0))
        if row.get("ce"):
            ce_strikes.append({"strike": sp, "oi": float(row["ce"].get("oi") or 0)})
        if row.get("pe"):
            pe_strikes.append({"strike": sp, "oi": float(row["pe"].get("oi") or 0)})

    ce_sorted = sorted(ce_strikes, key=lambda x: x["oi"], reverse=True)
    pe_sorted = sorted(pe_strikes, key=lambda x: x["oi"], reverse=True)

    ce_walls = ce_sorted[:num_strikes]
    pe_walls = pe_sorted[:num_strikes]

    highest_ce = ce_sorted[0] if ce_sorted else {"strike": 0, "oi": 0}
    highest_pe = pe_sorted[0] if pe_sorted else {"strike": 0, "oi": 0}

    # Immediate support/resistance: nearest wall to spot
    ce_above = [s for s in ce_walls if s["strike"] >= spot]
    pe_below = [s for s in pe_walls if s["strike"] <= spot]

    immediate_resistance = min(ce_above, key=lambda x: x["strike"])["strike"] if ce_above else highest_ce["strike"]
    immediate_support = max(pe_below, key=lambda x: x["strike"])["strike"] if pe_below else highest_pe["strike"]

    return {
        "highest_ce_oi_strike": highest_ce["strike"],
        "highest_pe_oi_strike": highest_pe["strike"],
        "ce_oi_walls": ce_walls,
        "pe_oi_walls": pe_walls,
        "immediate_resistance": immediate_resistance,
        "immediate_support": immediate_support,
    }


def compute_iv_skew(chain: list[dict], spot: float) -> dict:
    """
    IV Skew — compare implied volatility of ATM CE vs PE.

    • PE IV > CE IV → market pricing downside risk (bearish fear)
    • CE IV > PE IV → market pricing upside (bullish demand)

    Also computes IV smile shape: OTM put vs OTM call IV difference.

    Returns:
        {
            "atm_ce_iv":  float,
            "atm_pe_iv":  float,
            "iv_skew":    float,   # CE IV − PE IV (positive = bullish demand)
            "skew_bias":  str,     # "BULLISH" | "BEARISH" | "NEUTRAL"
            "avg_ce_iv":  float,   # average IV across all CE strikes
            "avg_pe_iv":  float,   # average IV across all PE strikes
        }
    """
    if not chain:
        return {
            "atm_ce_iv": 0, "atm_pe_iv": 0, "iv_skew": 0,
            "skew_bias": "NEUTRAL", "avg_ce_iv": 0, "avg_pe_iv": 0,
        }

    # Find ATM row
    atm_row = min(chain, key=lambda r: abs(float(r.get("strike_price", 0)) - spot))

    atm_ce_iv = float(atm_row["ce"]["iv"]) if atm_row.get("ce") else 0
    atm_pe_iv = float(atm_row["pe"]["iv"]) if atm_row.get("pe") else 0

    # Average IV across all strikes
    ce_ivs = [float(r["ce"]["iv"]) for r in chain if r.get("ce") and float(r["ce"].get("iv") or 0) > 0]
    pe_ivs = [float(r["pe"]["iv"]) for r in chain if r.get("pe") and float(r["pe"].get("iv") or 0) > 0]

    avg_ce_iv = sum(ce_ivs) / len(ce_ivs) if ce_ivs else 0
    avg_pe_iv = sum(pe_ivs) / len(pe_ivs) if pe_ivs else 0

    iv_skew = round(atm_ce_iv - atm_pe_iv, 2)

    if abs(iv_skew) < 1.0:
        skew_bias = "NEUTRAL"
    elif iv_skew > 0:
        skew_bias = "BULLISH"
    else:
        skew_bias = "BEARISH"

    return {
        "atm_ce_iv": round(atm_ce_iv, 2),
        "atm_pe_iv": round(atm_pe_iv, 2),
        "iv_skew": iv_skew,
        "skew_bias": skew_bias,
        "avg_ce_iv": round(avg_ce_iv, 2),
        "avg_pe_iv": round(avg_pe_iv, 2),
    }


def compute_oi_buildup(chain: list[dict], spot: float) -> dict:
    """
    Assess OI distribution relative to spot to gauge directional bias.

    • Heavy PE OI below spot = put writers supporting the downside = bullish
    • Heavy CE OI above spot = call writers capping the upside  = bearish
    • ITM OI concentration can signal hedging / protection flows

    Returns:
        {
            "ce_oi_above_spot":   float,   # CE OI at strikes > spot
            "ce_oi_below_spot":   float,   # CE OI at strikes < spot (ITM CE)
            "pe_oi_above_spot":   float,   # PE OI at strikes > spot (ITM PE)
            "pe_oi_below_spot":   float,   # PE OI at strikes < spot
            "oi_bias":            str,     # "BULLISH" | "BEARISH" | "NEUTRAL"
            "oi_bias_ratio":      float,   # pe_below / ce_above ratio
        }
    """
    ce_above = ce_below = pe_above = pe_below = 0.0

    for row in chain:
        sp = float(row.get("strike_price", 0))
        ce_oi = float(row["ce"]["oi"]) if row.get("ce") else 0
        pe_oi = float(row["pe"]["oi"]) if row.get("pe") else 0

        if sp >= spot:
            ce_above += ce_oi
            pe_above += pe_oi
        else:
            ce_below += ce_oi
            pe_below += pe_oi

    # Bias: PE writers below spot supporting = bullish
    # CE writers above spot capping = bearish
    # Compare: PE support vs CE resistance
    ratio = (pe_below / ce_above) if ce_above > 0 else 0.0

    if ratio > 1.2:
        bias = "BULLISH"
    elif ratio < 0.8:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "ce_oi_above_spot": ce_above,
        "ce_oi_below_spot": ce_below,
        "pe_oi_above_spot": pe_above,
        "pe_oi_below_spot": pe_below,
        "oi_bias": bias,
        "oi_bias_ratio": round(ratio, 3),
    }


# ── Composite Analysis ──────────────────────────────────────────


def analyze_option_chain(chain: list[dict], spot: float) -> dict:
    """
    Run all analytics on an option chain matrix and return a composite report.

    Args:
        chain: List of {strike_price, ce: {oi, iv, ltp, volume, ...}, pe: {...}}
        spot:  Current underlying spot price

    Returns:
        Full analytics dict with keys:
            pcr, max_pain, oi_concentration, iv_skew, oi_buildup,
            sentiment, directional_score
    """
    if not chain or spot <= 0:
        return _empty_analysis()

    pcr = compute_pcr(chain)
    max_pain = compute_max_pain(chain)
    oi_conc = compute_oi_concentration(chain, spot)
    iv_skew = compute_iv_skew(chain, spot)
    oi_build = compute_oi_buildup(chain, spot)

    # ── Composite Directional Score (-100 to +100) ──────────────
    # Positive = bullish consensus, Negative = bearish consensus.
    score = 0.0
    signals = []

    # 1. PCR OI bias (+/- 25 points)
    pcr_oi = pcr["pcr_oi"]
    if pcr_oi > 1.2:
        score += 25
        signals.append(f"PCR {pcr_oi:.2f} → BULLISH (put writing dominance)")
    elif pcr_oi > 1.0:
        score += 10
        signals.append(f"PCR {pcr_oi:.2f} → mildly bullish")
    elif pcr_oi < 0.7:
        score -= 25
        signals.append(f"PCR {pcr_oi:.2f} → BEARISH (call writing dominance)")
    elif pcr_oi < 0.9:
        score -= 10
        signals.append(f"PCR {pcr_oi:.2f} → mildly bearish")
    else:
        signals.append(f"PCR {pcr_oi:.2f} → neutral")

    # 2. Max-pain vs spot (+/- 20 points)
    mp = max_pain["max_pain_strike"]
    if mp > 0 and spot > 0:
        mp_dist_pct = ((mp - spot) / spot) * 100
        if mp_dist_pct > 0.5:
            score += 20
            signals.append(f"Max-Pain {mp:.0f} above spot → bullish pull")
        elif mp_dist_pct < -0.5:
            score -= 20
            signals.append(f"Max-Pain {mp:.0f} below spot → bearish pull")
        else:
            signals.append(f"Max-Pain {mp:.0f} ≈ spot → neutral")

    # 3. OI buildup direction (+/- 25 points)
    if oi_build["oi_bias"] == "BULLISH":
        score += 25
        signals.append("OI buildup → BULLISH (PE support under spot)")
    elif oi_build["oi_bias"] == "BEARISH":
        score -= 25
        signals.append("OI buildup → BEARISH (CE resistance above spot)")
    else:
        signals.append("OI buildup → neutral")

    # 4. IV skew (+/- 15 points)
    if iv_skew["skew_bias"] == "BULLISH":
        score += 15
        signals.append(f"IV Skew → BULLISH (CE IV > PE IV by {abs(iv_skew['iv_skew']):.1f})")
    elif iv_skew["skew_bias"] == "BEARISH":
        score -= 15
        signals.append(f"IV Skew → BEARISH (PE IV > CE IV by {abs(iv_skew['iv_skew']):.1f})")
    else:
        signals.append("IV Skew → neutral")

    # 5. OI support/resistance proximity (+/- 15 points)
    support = oi_conc["immediate_support"]
    resistance = oi_conc["immediate_resistance"]
    if support > 0 and resistance > 0 and spot > 0:
        dist_to_support = ((spot - support) / spot) * 100
        dist_to_resistance = ((resistance - spot) / spot) * 100

        if dist_to_support < 0.3:
            score += 15
            signals.append(f"Spot near PE wall {support:.0f} → strong support")
        elif dist_to_resistance < 0.3:
            score -= 15
            signals.append(f"Spot near CE wall {resistance:.0f} → strong resistance")

    # Clamp to [-100, +100]
    score = max(-100, min(100, score))

    if score >= 30:
        sentiment = "BULLISH"
    elif score <= -30:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"

    # ── Veteran Trader Interpretation (actionable narrative) ───────────
    support = oi_conc.get("immediate_support") or 0.0
    resistance = oi_conc.get("immediate_resistance") or 0.0
    range_bias = "RANGE"
    setup = "Wait for cleaner structure"

    if sentiment == "BULLISH":
        if resistance > 0 and spot < resistance:
            setup = f"Buy dips above {support:.0f}; watch breakout above {resistance:.0f}"
            range_bias = "UPSIDE_PRESSURE"
        else:
            setup = "Trend continuation likely; avoid chasing far from support"
            range_bias = "TRENDING_UP"
    elif sentiment == "BEARISH":
        if support > 0 and spot > support:
            setup = f"Sell rallies below {resistance:.0f}; watch breakdown below {support:.0f}"
            range_bias = "DOWNSIDE_PRESSURE"
        else:
            setup = "Trend continuation likely; avoid fresh shorts into support"
            range_bias = "TRENDING_DOWN"
    else:
        if support > 0 and resistance > 0:
            setup = f"Range trade between {support:.0f} and {resistance:.0f} until OI shifts"

    invalidation = "Reassess if OI walls shift materially"
    if sentiment == "BULLISH" and support > 0:
        invalidation = f"Bullish view weakens on sustained spot below {support:.0f}"
    elif sentiment == "BEARISH" and resistance > 0:
        invalidation = f"Bearish view weakens on sustained spot above {resistance:.0f}"

    confidence = min(100, max(0, 50 + abs(int(round(score))) // 2))

    veteran_view = {
        "market_regime": range_bias,
        "setup": setup,
        "invalidation": invalidation,
        "confidence": int(confidence),
        "execution_note": "Use OC as context; trigger entries only with price-action confirmation.",
    }

    return {
        "pcr": pcr,
        "max_pain": max_pain,
        "oi_concentration": oi_conc,
        "iv_skew": iv_skew,
        "oi_buildup": oi_build,
        "sentiment": sentiment,
        "directional_score": int(round(score)),
        "signals": signals,
        "veteran_view": veteran_view,
        "spot_price": spot,
    }


def _empty_analysis() -> dict:
    """Return a safe empty-state analysis dict."""
    return {
        "pcr": {"pcr_oi": 0, "pcr_volume": 0, "total_ce_oi": 0, "total_pe_oi": 0, "total_ce_vol": 0, "total_pe_vol": 0},
        "max_pain": {"max_pain_strike": 0, "loss_at_max_pain": 0},
        "oi_concentration": {"highest_ce_oi_strike": 0, "highest_pe_oi_strike": 0, "ce_oi_walls": [], "pe_oi_walls": [], "immediate_resistance": 0, "immediate_support": 0},
        "iv_skew": {"atm_ce_iv": 0, "atm_pe_iv": 0, "iv_skew": 0, "skew_bias": "NEUTRAL", "avg_ce_iv": 0, "avg_pe_iv": 0},
        "oi_buildup": {"ce_oi_above_spot": 0, "ce_oi_below_spot": 0, "pe_oi_above_spot": 0, "pe_oi_below_spot": 0, "oi_bias": "NEUTRAL", "oi_bias_ratio": 0},
        "sentiment": "NEUTRAL",
        "directional_score": 0,
        "signals": [],
        "veteran_view": {
            "market_regime": "RANGE",
            "setup": "Insufficient option-chain data",
            "invalidation": "Wait for fresh data",
            "confidence": 0,
            "execution_note": "No actionable edge from OC yet.",
        },
        "spot_price": 0,
    }
