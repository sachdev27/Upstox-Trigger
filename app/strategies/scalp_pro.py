import pandas as pd
from datetime import datetime

from app.strategies.base import BaseStrategy, StrategyConfig
from app.strategies.indicators import ema, rsi, vwap, atr, adx as calc_adx
from app.orders.models import TradeSignal, TransactionType

class ScalpPro(BaseStrategy):
    """
    High-speed scalping strategy targeting small points frequently.
    Uses Fast/Slow EMA crossover, anchored VWAP trend filter, and RSI momentum confirmation.
    """

    @classmethod
    def default_params(cls) -> dict:
        return {
            # ── Core Signal ─────────────────────────────────────
            "fast_ema": 9,
            "slow_ema": 21,
            "rsi_period": 14,
            "rsi_buy_thresh": 50,
            "rsi_sell_thresh": 50,
            "use_vwap_filter": True,
            "atr_period": 14,

            # ── HFT Pullback Controls ────────────────────────────
            # In scalping, strict VWAP hard-block can reject good pullback entries.
            # Allow a small ATR-based VWAP tolerance ONLY when trend strength is high.
            "vwap_pullback_tolerance_atr": 0.15,   # 0 disables tolerance (strict VWAP)
            "vwap_pullback_adx_min": 25,           # require strong trend for tolerance

            # ── Risk (single-exit mode) ──────────────────────────
            "sl_atr_multiplier": 1.0,   # stop-loss distance = sl_atr_mult × ATR
            "tp_atr_multiplier": 2.0,   # take-profit distance = tp_atr_mult × ATR

            # ── Partial Booking (3-tier scaled exit) ─────────────
            # Enable to book profits progressively instead of all-or-nothing.
            # Engine automatically moves SL to breakeven after TP1 hit.
            "partial_tp_enabled":   True,
            "tp1_atr_mult":         1.0,   # TP1 distance  → book tp1_book_pct %
            "tp2_atr_mult":         1.5,   # TP2 distance  → book tp2_book_pct %
            "tp3_atr_mult":         2.0,   # TP3 / trail   → book remainder
            "tp1_book_pct":         40,    # % of position to close at TP1
            "tp2_book_pct":         40,    # % of position to close at TP2
            # remainder (20%) held until TP3 or trailing SL

            # ── Trailing Stop-Loss ────────────────────────────────
            "enable_trailing_sl":   True,
            "trailing_atr_mult":    1.0,   # trailing SL distance = trailing_atr_mult × ATR

            # ── Swarm Orders (multi-lot per signal) ──────────────
            # swarm_count > 1 fires N parallel lots on a single signal.
            # Each lot gets a different TP level (tp1, tp2, tp3, then tp3 for rest).
            # All lots share the same SL.  Use swarm_count = 1 to disable.
            "swarm_count":          1,     # 1–5 simultaneous lots

            # ── Option / Quantity Control ─────────────────────────
            # Number of lots per signal.  Set from the frontend strategy panel.
            # For option buying: set to the number of contracts you want per trade.
            "quantity":             1,

            # ── Hard Quality Gates (block trade BEFORE signal is built) ────────
            # ADX gate — skip when market is choppy/ranging.
            # ADX < threshold means no meaningful trend; crossovers are unreliable.
            # Typical: 20 (lenient) … 25 (standard) … 30 (strict).
            "adx_period":           14,
            "adx_threshold":        20,    # 0 = disabled

            # Volume spike gate — only take crossovers backed by conviction volume.
            # Bar volume must be > volume_spike_mult × SMA(volume_lookback_bars).
            # Typical: 1.2 (lenient) … 1.5 (standard) … 2.0 (strict).
            "volume_spike_mult":    1.2,   # 0 = disabled
            "volume_lookback_bars": 20,

            # ── Selectivity / Probability Filters ────────────────
            # Signals below this score are skipped by the engine.
            "min_confidence_score": 60,
            # Per strategy-cycle, execute only top-N scored candidates.
            "top_n_signals_per_cycle": 2,

            # ── RSI Extreme-Zone Protection ───────────────────────
            # Block trades when RSI is in exhaustion territory:
            #   • Don't SELL when RSI is already deeply OVERSOLD  (bounce risk)
            #   • Don't BUY  when RSI is already deeply OVERBOUGHT (reversal risk)
            # These exits are where weak-hands capitulate; entering there is chasing.
            # Set to 0 to disable each limit individually.
            "rsi_oversold_max":     28,    # don't SELL below this RSI  (e.g. 25-30)
            "rsi_overbought_min":   72,    # don't BUY  above this RSI  (e.g. 70-75)

            # ── Option Chain Insight (OC) ─────────────────────────
            # Real-time option chain analysis (PCR, OI, IV, Max-Pain)
            # enriches signals with market sentiment from derivatives data.
            "use_oc_insight":           False,  # enable OC analysis in pipeline
            "oc_confidence_boost":      10,     # points added when OC aligns with signal
            "oc_confidence_penalty":    15,     # points removed when OC contradicts
            "oc_block_contradictions":  False,  # block signal if OC strongly disagrees
            "oc_block_threshold":       60,     # directional_score threshold for block

            # Broker truth mode
            "require_broker_fill_confirmation": True,
        }

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _compute_confidence(
        self,
        side: str,
        close: float,
        fast_curr: float,
        fast_prev: float,
        slow_curr: float,
        slow_prev: float,
        rsi_val: float,
        atr_val: float,
        vwap_val: float | None,
        adx_val: float = 50.0,
    ) -> tuple[int, dict]:
        """Build a bounded 0-100 confidence score from trend, momentum, and quality factors."""
        p = self.params
        atr_safe = max(float(atr_val or 0.0), 1e-9)

        # Trend extension after crossover relative to ATR.
        spread = abs(float(fast_curr) - float(slow_curr))
        trend_score = self._clamp((spread / atr_safe) * 28.0, 0.0, 28.0)

        # Slope alignment: how strongly fast EMA is pulling away from slow EMA.
        fast_slope = float(fast_curr) - float(fast_prev)
        slow_slope = float(slow_curr) - float(slow_prev)
        slope_delta = fast_slope - slow_slope if side == "BUY" else slow_slope - fast_slope
        slope_score = self._clamp((slope_delta / atr_safe) * 22.0, 0.0, 22.0)

        # RSI momentum distance from threshold in trade direction.
        if side == "BUY":
            rsi_edge = float(rsi_val) - float(p["rsi_buy_thresh"])
        else:
            rsi_edge = float(p["rsi_sell_thresh"]) - float(rsi_val)
        momentum_score = self._clamp((rsi_edge / 20.0) * 18.0, 0.0, 18.0)

        # ADX quality: stronger trend → higher score.
        # ADX=20 → 0 pts (hard gate boundary); ADX=50 → 12 pts; ADX≥70 → 14 pts max.
        adx_safe = float(adx_val or 25.0)
        adx_score = self._clamp((adx_safe - 20.0) / 50.0 * 14.0, 0.0, 14.0)

        # Volatility quality: avoid very low-volatility chop; reward moderate ATR regimes.
        atr_pct = (atr_safe / max(float(close or 0.0), 1e-9)) * 100.0
        if atr_pct < 0.07:
            vol_score = 2.0
        elif atr_pct > 1.8:
            vol_score = 8.0
        else:
            vol_score = 10.0

        # VWAP alignment quality bonus when enabled.
        vwap_score = 0.0
        if p.get("use_vwap_filter") and vwap_val is not None:
            dist = abs(float(close) - float(vwap_val))
            vwap_score = self._clamp((dist / atr_safe) * 16.0, 0.0, 16.0)

        total = int(round(self._clamp(trend_score + slope_score + momentum_score + adx_score + vol_score + vwap_score, 0.0, 100.0)))
        parts = {
            "trend": round(trend_score, 2),
            "slope": round(slope_score, 2),
            "momentum": round(momentum_score, 2),
            "adx": round(adx_score, 2),
            "volatility": round(vol_score, 2),
            "vwap": round(vwap_score, 2),
        }
        return total, parts

    def _get_indicators(self, df: pd.DataFrame) -> dict:
        """Calculate and cache indicators for the current dataframe to avoid redundant math."""
        if len(df) == 0: return {}

        # Identity check: length + last timestamp
        df_id = (len(df), df["time"].iloc[-1] if "time" in df.columns else 0)
        if hasattr(self, "_last_df_id") and self._last_df_id == df_id:
            return self._indicator_cache

        p = self.params
        res = {
            "fast": ema(df["close"], p["fast_ema"]),
            "slow": ema(df["close"], p["slow_ema"]),
            "rsi": rsi(df["close"], p["rsi_period"]),
            "vwap": vwap(df) if p["use_vwap_filter"] else None,
            "atr": atr(df, p["atr_period"]),
            "close": df["close"],
        }

        # ADX hard-gate indicator
        adx_thresh = float(p.get("adx_threshold", 0))
        if adx_thresh > 0 and "high" in df.columns and "low" in df.columns:
            res["adx"] = calc_adx(df, int(p.get("adx_period", 14)))["adx"]
        else:
            res["adx"] = None

        # Volume spike hard-gate indicator
        vol_mult = float(p.get("volume_spike_mult", 0))
        if vol_mult > 0 and "volume" in df.columns:
            res["vol_ma"] = df["volume"].rolling(int(p.get("volume_lookback_bars", 20))).mean()
        else:
            res["vol_ma"] = None

        self._last_df_id = df_id
        self._indicator_cache = res
        return res

    def get_dashboard_state(
        self, df: "pd.DataFrame", htf_df: "pd.DataFrame | None" = None
    ) -> dict:
        """Calculate state for the UI overlay — shown in the strategy feedback panel."""
        p = self.params
        if len(df) < p["slow_ema"]:
            return {}

        ind = self._get_indicators(df)

        close     = float(ind["close"].iloc[-1])
        fast_curr = float(ind["fast"].iloc[-1])
        slow_curr = float(ind["slow"].iloc[-1])
        rsi_val   = float(ind["rsi"].iloc[-1])

        trend = "BULLISH" if fast_curr > slow_curr else "BEARISH"

        # ADX status — computed first because VWAP pullback tolerance depends on trend strength
        adx_thresh = float(p.get("adx_threshold", 0))
        adx_now = None
        adx_label  = "DISABLED"
        if adx_thresh > 0 and ind.get("adx") is not None:
            adx_now = float(ind["adx"].iloc[-1])
            status  = "TRENDING" if adx_now >= adx_thresh else "RANGING \u26a0"
            adx_label = f"{status} ({adx_now:.1f})"

        # VWAP alignment with optional pullback tolerance for strong-trend scalping
        if not p["use_vwap_filter"]:
            vwap_state = "PASS"
        else:
            vwap_now = float(ind["vwap"].iloc[-1])
            atr_now = max(float(ind["atr"].iloc[-1]), 1e-9)
            tol_atr = float(p.get("vwap_pullback_tolerance_atr", 0.0))
            tol_abs = tol_atr * atr_now
            adx_req = float(p.get("vwap_pullback_adx_min", 25))
            strong_adx = (adx_now is not None and adx_now >= adx_req)

            if trend == "BULLISH" and close > vwap_now:
                vwap_state = "PASS (Above)"
            elif trend == "BEARISH" and close < vwap_now:
                vwap_state = "PASS (Below)"
            elif trend == "BULLISH" and tol_abs > 0 and strong_adx and close >= (vwap_now - tol_abs):
                vwap_state = f"PASS (Pullback <= {tol_atr:.2f} ATR)"
            elif trend == "BEARISH" and tol_abs > 0 and strong_adx and close <= (vwap_now + tol_abs):
                vwap_state = f"PASS (Pullback <= {tol_atr:.2f} ATR)"
            else:
                vwap_state = "FAIL"

        # RSI zone label — mirrors the extreme-protection gate thresholds
        oversold_max   = float(p.get("rsi_oversold_max",  28))
        overbought_min = float(p.get("rsi_overbought_min", 72))
        if rsi_val <= oversold_max:
            rsi_zone = f"OVERSOLD (\u26a0 {rsi_val:.1f}) \u2014 SELL blocked"
        elif rsi_val >= overbought_min:
            rsi_zone = f"OVERBOUGHT (\u26a0 {rsi_val:.1f}) \u2014 BUY blocked"
        elif rsi_val < 40:
            rsi_zone = f"WEAK ({rsi_val:.1f})"
        elif rsi_val > 60:
            rsi_zone = f"STRONG ({rsi_val:.1f})"
        else:
            rsi_zone = f"NEUTRAL ({rsi_val:.1f})"

        # Block-reason list for transparency
        blocks: list = []
        if vwap_state == "FAIL":
            reason = (
                "Price below VWAP pullback tolerance (long blocked)"
                if trend == "BULLISH"
                else "Price above VWAP pullback tolerance (short blocked)"
            )
            blocks.append(reason)
        if trend == "BEARISH" and rsi_val <= oversold_max:
            blocks.append(f"RSI {rsi_val:.1f} too oversold to short")
        if trend == "BULLISH" and rsi_val >= overbought_min:
            blocks.append(f"RSI {rsi_val:.1f} too overbought to buy")
        if adx_thresh > 0 and ind.get("adx") is not None and float(ind["adx"].iloc[-1]) < adx_thresh:
            adx_cur = float(ind["adx"].iloc[-1])
            blocks.append(f"ADX {adx_cur:.1f} < {adx_thresh:.0f} (ranging)")

        state: dict = {
            "EMA Trend":   trend,
            "VWAP Filter": vwap_state,
            "RSI Zone":    rsi_zone,
            "RSI Value":   round(rsi_val, 2),
            "ATR":         round(float(ind["atr"].iloc[-1]), 2),
        }
        if adx_label != "DISABLED":
            state["ADX"] = adx_label
        if blocks:
            state["Signal Blocked"] = " | ".join(blocks)
        return state

    def on_candle(
        self,
        df: "pd.DataFrame",
        htf_df: "pd.DataFrame | None" = None,
    ) -> "TradeSignal | None":
        p = self.params
        if len(df) < p["slow_ema"]:
            return None

        ind = self._get_indicators(df)

        fast_curr = float(ind["fast"].iloc[-1])
        fast_prev = float(ind["fast"].iloc[-2])
        slow_curr = float(ind["slow"].iloc[-1])
        slow_prev = float(ind["slow"].iloc[-2])

        # EMA Crossovers
        buy_cross  = fast_curr > slow_curr and fast_prev <= slow_prev
        sell_cross = fast_curr < slow_curr and fast_prev >= slow_prev

        if not buy_cross and not sell_cross:
            return None

        close = float(ind["close"].iloc[-1])

        # ── Hard gate 1: ADX trend-strength filter ──────────────────────────
        # Low ADX means the market is ranging/choppy; crossovers cancel quickly.
        adx_thresh = float(p.get("adx_threshold", 0))
        if adx_thresh > 0 and ind.get("adx") is not None:
            try:
                adx_now = float(ind["adx"].iloc[-1])
                if adx_now < adx_thresh:
                    return None          # market too choppy — skip trade
            except Exception:
                adx_now = 50.0           # fallback: neutral (don't block)
        else:
            adx_now = 50.0               # ADX filter disabled

        # ── Hard gate 2: volume spike filter ────────────────────────────────
        # Real breakouts are backed by above-average volume (buyer/seller conviction).
        vol_mult = float(p.get("volume_spike_mult", 0))
        if vol_mult > 0 and ind.get("vol_ma") is not None and "volume" in df.columns:
            try:
                vol_now = float(df["volume"].iloc[-1])
                vol_avg = float(ind["vol_ma"].iloc[-1])
                if vol_avg > 0 and vol_now < vol_avg * vol_mult:
                    return None          # insufficient volume conviction — skip
            except Exception:
                pass                     # if volume data is missing, don't block

        rsi_val       = float(ind["rsi"].iloc[-1])
        oversold_max  = float(p.get("rsi_oversold_max",  28))
        overbought_min= float(p.get("rsi_overbought_min", 72))

        # Long conditions
        if buy_cross:
            # VWAP gate with optional strong-trend pullback tolerance for scalping.
            if p["use_vwap_filter"]:
                vwap_now = float(ind["vwap"].iloc[-1])
                atr_now = max(float(ind["atr"].iloc[-1]), 1e-9)
                tol_atr = float(p.get("vwap_pullback_tolerance_atr", 0.0))
                tol_abs = tol_atr * atr_now
                adx_req = float(p.get("vwap_pullback_adx_min", 25))
                allow_pullback = (tol_abs > 0 and adx_now >= adx_req and close >= (vwap_now - tol_abs))
                if close < vwap_now and not allow_pullback:
                    return None
            # RSI must exceed momentum threshold
            if rsi_val < p["rsi_buy_thresh"]:
                return None
            # ── RSI extreme-zone protection: don't buy into overbought exhaustion ──
            if overbought_min > 0 and rsi_val >= overbought_min:
                return None

            atr_val  = float(ind["atr"].iloc[-1])
            sl_dist  = atr_val * p["sl_atr_multiplier"]
            tp1_mult = float(p.get("tp1_atr_mult", 1.0))
            tp2_mult = max(tp1_mult, float(p.get("tp2_atr_mult", 1.5)))
            tp3_mult = max(tp2_mult, float(p.get("tp3_atr_mult", p.get("tp_atr_multiplier", 2.0))))
            tp_dist  = atr_val * tp3_mult
            score, score_parts = self._compute_confidence(
                "BUY", close, fast_curr, fast_prev, slow_curr, slow_prev,
                rsi_val, atr_val,
                float(ind["vwap"].iloc[-1]) if p.get("use_vwap_filter") and ind.get("vwap") is not None else None,
                adx_val=adx_now,
            )

            return TradeSignal(
                strategy_name=self.config.name,
                instrument_key="",  # injected by engine
                action=TransactionType.BUY,
                price=close,
                quantity=int(p.get("quantity", 1)),
                stop_loss=close - sl_dist,
                take_profit=close + tp_dist,
                confidence_score=score,
                metadata={
                    "atr":                atr_val,
                    "tp1":                close + atr_val * tp1_mult,
                    "tp2":                close + atr_val * tp2_mult,
                    "tp3":                close + tp_dist,
                    "tp1_book_pct":       p.get("tp1_book_pct", 40),
                    "tp2_book_pct":       p.get("tp2_book_pct", 40),
                    "partial_tp_enabled": p.get("partial_tp_enabled", True),
                    "trailing_atr_mult":  p.get("trailing_atr_mult", 1.0),
                    "swarm_count":        int(p.get("swarm_count", 1)),
                    "score_breakdown":    score_parts,
                },
            )

        # Short conditions
        if sell_cross:
            # VWAP gate with optional strong-trend pullback tolerance for scalping.
            if p["use_vwap_filter"]:
                vwap_now = float(ind["vwap"].iloc[-1])
                atr_now = max(float(ind["atr"].iloc[-1]), 1e-9)
                tol_atr = float(p.get("vwap_pullback_tolerance_atr", 0.0))
                tol_abs = tol_atr * atr_now
                adx_req = float(p.get("vwap_pullback_adx_min", 25))
                allow_pullback = (tol_abs > 0 and adx_now >= adx_req and close <= (vwap_now + tol_abs))
                if close > vwap_now and not allow_pullback:
                    return None
            # RSI must be below momentum threshold
            if rsi_val > p["rsi_sell_thresh"]:
                return None
            # ── RSI extreme-zone protection: don't short into deeply oversold territory ──
            if oversold_max > 0 and rsi_val <= oversold_max:
                return None

            atr_val  = float(ind["atr"].iloc[-1])
            sl_dist  = atr_val * p["sl_atr_multiplier"]
            tp1_mult = float(p.get("tp1_atr_mult", 1.0))
            tp2_mult = max(tp1_mult, float(p.get("tp2_atr_mult", 1.5)))
            tp3_mult = max(tp2_mult, float(p.get("tp3_atr_mult", p.get("tp_atr_multiplier", 2.0))))
            tp_dist  = atr_val * tp3_mult
            score, score_parts = self._compute_confidence(
                "SELL", close, fast_curr, fast_prev, slow_curr, slow_prev,
                rsi_val, atr_val,
                float(ind["vwap"].iloc[-1]) if p.get("use_vwap_filter") and ind.get("vwap") is not None else None,
                adx_val=adx_now,
            )

            return TradeSignal(
                strategy_name=self.config.name,
                instrument_key="",
                action=TransactionType.SELL,
                price=close,
                quantity=int(p.get("quantity", 1)),
                stop_loss=close + sl_dist,
                take_profit=close - tp_dist,
                confidence_score=score,
                metadata={
                    "atr":                atr_val,
                    "tp1":                close - atr_val * tp1_mult,
                    "tp2":                close - atr_val * tp2_mult,
                    "tp3":                close - tp_dist,
                    "tp1_book_pct":       p.get("tp1_book_pct", 40),
                    "tp2_book_pct":       p.get("tp2_book_pct", 40),
                    "partial_tp_enabled": p.get("partial_tp_enabled", True),
                    "trailing_atr_mult":  p.get("trailing_atr_mult", 1.0),
                    "swarm_count":        int(p.get("swarm_count", 1)),
                    "score_breakdown":    score_parts,
                },
            )

        return None

    def compute_exit(
        self,
        entry_price: float,
        position_side: str,  # "LONG" or "SHORT"
        current_price: float,
        df: pd.DataFrame,
    ) -> dict:
        """
        Dynamic exit levels for active positions.
        Returns stop_loss, take_profit, and tier levels (tp1/tp2/tp3).
        """
        p = self.params
        if len(df) < p["slow_ema"]:
            return {"stop_loss": None, "take_profit": None, "trailing_stop": None}

        atr_val  = atr(df, p["atr_period"]).iloc[-1]
        sl_dist  = atr_val * p["sl_atr_multiplier"]
        tp3_dist = atr_val * p.get("tp3_atr_mult", p.get("tp_atr_multiplier", 2.0))
        trail_d  = atr_val * p.get("trailing_atr_mult", 1.0)

        if position_side == "LONG":
            return {
                "stop_loss":     entry_price - sl_dist,
                "take_profit":   entry_price + tp3_dist,
                "trailing_stop": trail_d if p.get("enable_trailing_sl") else None,
                "tp1":           entry_price + atr_val * p.get("tp1_atr_mult", sl_dist / atr_val),
                "tp2":           entry_price + atr_val * p.get("tp2_atr_mult", tp3_dist / atr_val * 0.6),
                "tp3":           entry_price + tp3_dist,
            }
        else:
            return {
                "stop_loss":     entry_price + sl_dist,
                "take_profit":   entry_price - tp3_dist,
                "trailing_stop": trail_d if p.get("enable_trailing_sl") else None,
                "tp1":           entry_price - atr_val * p.get("tp1_atr_mult", sl_dist / atr_val),
                "tp2":           entry_price - atr_val * p.get("tp2_atr_mult", tp3_dist / atr_val * 0.6),
                "tp3":           entry_price - tp3_dist,
            }
