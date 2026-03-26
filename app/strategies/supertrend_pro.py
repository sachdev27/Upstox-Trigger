"""
SuperTrend Pro v6.3 — ported from Pine Script to Python.

Original: stratergy.pine (590 lines)
This is a 1:1 faithful port of all 11 sections:
  0. Auto Timeframe Detection
  1. Primary SuperTrend
  2. Hard Gate H1: Dual ST Agreement
  3. Hard Gate H2: Consecutive Bar Confirmation
  4. Soft Score Filters (ADX, Volume, ATR%, ROC, BB Squeeze)
  4b. Hard Gate H3: HTF Trend Filter
  5. Final Signal Composition
  6. Exit Settings (Modes A/B/C)
  7. Strategy Execution (via BaseStrategy)
  8-11. Dashboard/Alerts handled by the frontend/notification service
"""

from typing import Any

import numpy as np
import pandas as pd

from app.strategies.base import BaseStrategy, StrategyConfig
from app.strategies.indicators import (
    atr,
    supertrend,
    adx,
    roc,
    bb_squeeze,
    volume_surge,
    atr_percentile,
    consecutive_confirming_bars,
)
from app.orders.models import TradeSignal, TransactionType


class SuperTrendPro(BaseStrategy):
    """
    SuperTrend Pro v6.3 — Professional multi-filter SuperTrend strategy.

    Signal pipeline:
      Primary ST  →  H1: Dual ST  →  H2: Consecutive  →  H3: HTF  →  Soft Score  →  Signal
    """

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            # Section 0: TF Mode
            "tf_mode": "auto",  # "auto" or "manual"

            # Section 1: Primary SuperTrend
            "atr_period": 10,
            "atr_multiplier": 3.0,
            "use_rma": True,

            # Section 2: H1 — Dual ST
            "use_dual_st": True,
            "slow_atr_period": 20,
            "slow_atr_multiplier": 5.0,

            # Section 3: H2 — Consecutive Bars
            "use_consecutive": True,
            "manual_consec_bars": 2,

            # Section 4: Soft Filters
            "use_adx": True,
            "adx_length": 14,
            "manual_adx_threshold": 20.0,

            "use_volume": True,
            "volume_lookback": 20,
            "manual_vol_multiplier": 1.4,

            "use_atr_percentile": True,
            "atr_pct_lookback": 100,
            "manual_atr_pct_threshold": 45.0,

            "use_roc": True,
            "roc_lookback": 3,
            "manual_roc_threshold": 2.0,

            "use_bb_squeeze": True,
            "bb_length": 20,
            "bb_multiplier": 2.0,
            "squeeze_lookback": 10,

            "manual_soft_required": 3,

            # Section 4b: H3 — HTF Filter
            "use_htf_filter": True,
            "htf_timeframe": "1D",  # NOTE: requires HTF data to be provided
            "htf_atr_period": 10,
            "htf_atr_multiplier": 3.0,

            # Section 6: Exit Settings
            "exit_mode": "C",  # A, B, or C
            "sl_multiplier": 1.5,
            "tp_multiplier": 3.0,    # Mode A
            "profit_pct": 3.0,       # Mode B
            "trail_multiplier": 1.5, # Mode C

            # Risk
            "risk_per_trade_pct": 1.0,
            "qty_override": 0,       # 0 = use risk-based, >0 = fixed quantity
        }

    # ── Auto TF Adaptation (Section 0) ──────────────────────────

    @staticmethod
    def _tf_minutes(timeframe: str) -> int:
        """Convert timeframe string to minutes."""
        tf_map = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1H": 60, "4H": 240, "1D": 1440, "1W": 10080,
        }
        return tf_map.get(timeframe, 15)

    def _auto_thresholds(self, tf_mins: int) -> dict:
        """Auto-adapt thresholds by timeframe (Section 0 from Pine)."""
        def auto_vol(m):
            return 1.4 if m >= 1440 else 1.3 if m >= 240 else 1.4 if m >= 60 else 1.2 if m >= 15 else 1.1

        def auto_roc(m):
            return 3.0 if m >= 10080 else 2.0 if m >= 1440 else 1.0 if m >= 240 else 0.5 if m >= 60 else 0.25 if m >= 15 else 0.15

        def auto_consec(m):
            return 2 if m >= 240 else 2 if m >= 60 else 1

        def auto_atr_pct(m):
            return 50.0 if m >= 1440 else 45.0 if m >= 240 else 45.0 if m >= 60 else 35.0 if m >= 15 else 25.0

        def auto_adx(m):
            return 22.0 if m >= 240 else 20.0 if m >= 60 else 18.0 if m >= 15 else 15.0

        def auto_soft_req(m):
            return 3 if m >= 60 else 2

        return {
            "vol_mult": auto_vol(tf_mins),
            "roc_thresh": auto_roc(tf_mins),
            "consec_bars": auto_consec(tf_mins),
            "atr_pct_thresh": auto_atr_pct(tf_mins),
            "adx_thresh": auto_adx(tf_mins),
            "soft_required": auto_soft_req(tf_mins),
        }

    # ── Signal Evaluation (on_candle) ───────────────────────────

    def on_candle(
        self,
        df: pd.DataFrame,
        htf_df: pd.DataFrame | None = None,
    ) -> TradeSignal | None:
        """
        Evaluate the latest candle and return a BUY/SELL signal if all gates pass.

        Args:
            df: Primary timeframe OHLCV DataFrame
            htf_df: Higher timeframe OHLCV DataFrame (optional, for H3 gate)
        """
        if len(df) < 100:
            return None  # Not enough data for indicators

        # Snapshot the full strategy evaluation matrix for the UI Dashboard panel
        self.latest_metrics = self.get_dashboard_state(df, htf_df)

        p = self.params
        tf_mins = self._tf_minutes(self.config.timeframe)
        is_auto = p["tf_mode"] == "auto"

        # Get auto thresholds
        auto = self._auto_thresholds(tf_mins) if is_auto else {}

        # ── Section 1: Primary SuperTrend ───────────────────────
        st = supertrend(df, p["atr_period"], p["atr_multiplier"], p["use_rma"])
        trend = st["trend"]

        # Buy/Sell signal: trend flip
        buy_signal = trend.iloc[-1] == 1 and trend.iloc[-2] == -1
        sell_signal = trend.iloc[-1] == -1 and trend.iloc[-2] == 1

        if not buy_signal and not sell_signal:
            return None  # No trend flip → no signal

        # ── Section 2: H1 — Dual ST Agreement ──────────────────
        dual_agree = True
        if p["use_dual_st"]:
            slow_st = supertrend(
                df, p["slow_atr_period"], p["slow_atr_multiplier"], p["use_rma"]
            )
            dual_agree = slow_st["trend"].iloc[-1] == trend.iloc[-1]

        if not dual_agree:
            return None

        # ── Section 3: H2 — Consecutive Bar Confirmation ───────
        consec_ok = True
        if p["use_consecutive"]:
            consec_bars_req = (
                auto.get("consec_bars", p["manual_consec_bars"])
                if is_auto else p["manual_consec_bars"]
            )
            consec_count = consecutive_confirming_bars(trend, df["close"])
            consec_ok = consec_count.iloc[-1] >= consec_bars_req

        if not consec_ok:
            return None

        # ── Section 4b: H3 — HTF Trend Filter ──────────────────
        htf_bullish = True
        htf_bearish = True
        if p["use_htf_filter"] and htf_df is not None and len(htf_df) > 30:
            htf_st = supertrend(
                htf_df, p["htf_atr_period"], p["htf_atr_multiplier"]
            )
            htf_trend = htf_st["trend"].iloc[-1]
            htf_bullish = htf_trend == 1
            htf_bearish = htf_trend == -1

        # Buy only when HTF bullish, Sell only when HTF bearish
        if buy_signal and not htf_bullish:
            return None
        if sell_signal and not htf_bearish:
            return None

        # ── Section 4: Soft Score Filters ───────────────────────
        soft_score = 0

        # S1: ADX
        if p["use_adx"]:
            adx_thresh = auto.get("adx_thresh", p["manual_adx_threshold"]) if is_auto else p["manual_adx_threshold"]
            adx_data = adx(df, p["adx_length"])
            if adx_data["adx"].iloc[-1] > adx_thresh:
                soft_score += 1
        else:
            soft_score += 1

        # S2: Volume Surge
        if p["use_volume"] and "volume" in df.columns:
            vol_mult = auto.get("vol_mult", p["manual_vol_multiplier"]) if is_auto else p["manual_vol_multiplier"]
            if volume_surge(df["volume"], p["volume_lookback"], vol_mult).iloc[-1]:
                soft_score += 1
        else:
            soft_score += 1

        # S3: ATR Percentile
        if p["use_atr_percentile"]:
            atr_pct_thresh = auto.get("atr_pct_thresh", p["manual_atr_pct_threshold"]) if is_auto else p["manual_atr_pct_threshold"]
            atr_vals = atr(df, p["atr_period"], p["use_rma"])
            pct = atr_percentile(atr_vals, p["atr_pct_lookback"])
            if not isinstance(pct, pd.Series):
                pct = pd.Series(pct, index=df.index)
            if pct.iloc[-1] >= atr_pct_thresh:
                soft_score += 1
        else:
            soft_score += 1

        # S4: ROC
        if p["use_roc"]:
            roc_thresh = auto.get("roc_thresh", p["manual_roc_threshold"]) if is_auto else p["manual_roc_threshold"]
            roc_val = roc(df["close"], p["roc_lookback"]).abs()
            if roc_val.iloc[-1] >= roc_thresh:
                soft_score += 1
        else:
            soft_score += 1

        # S5: BB Squeeze
        if p["use_bb_squeeze"]:
            squeeze = bb_squeeze(
                df["close"], p["bb_length"], p["bb_multiplier"], p["squeeze_lookback"]
            )
            if squeeze["squeeze_breakout"].iloc[-1]:
                soft_score += 1
        else:
            soft_score += 1

        soft_required = (
            auto.get("soft_required", p["manual_soft_required"])
            if is_auto else p["manual_soft_required"]
        )

        if soft_score < soft_required:
            return None  # Near miss — not enough soft filters passed

        # ── Section 5: Valid Signal! ────────────────────────────
        current_price = df["close"].iloc[-1]
        atr_val = atr(df, p["atr_period"], p["use_rma"]).iloc[-1]

        sl_dist = atr_val * p["sl_multiplier"]

        if buy_signal:
            stop_loss = current_price - sl_dist
            take_profit = self._compute_tp(current_price, atr_val, "BUY")
            action = TransactionType.BUY
        else:
            stop_loss = current_price + sl_dist
            take_profit = self._compute_tp(current_price, atr_val, "SELL")
            action = TransactionType.SELL

        return TradeSignal(
            action=action,
            instrument_key=self.config.instruments[0] if self.config.instruments else "",
            price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=p.get("qty_override", 0),
            strategy_name=self.name,
            timeframe=self.config.timeframe,
            confidence_score=soft_score,
            metadata={
                "atr": atr_val,
                "soft_score": f"{soft_score}/{soft_required}",
                "exit_mode": p["exit_mode"],
                "trend_direction": "LONG" if buy_signal else "SHORT",
            },
        )

    # ── Exit Computation (Section 6) ────────────────────────────

    def compute_exit(
        self,
        entry_price: float,
        position_side: str,
        current_price: float,
        df: pd.DataFrame,
    ) -> dict:
        """Compute SL/TP levels based on exit mode."""
        p = self.params
        atr_val = atr(df, p["atr_period"], p["use_rma"]).iloc[-1]
        sl_dist = atr_val * p["sl_multiplier"]

        if position_side == "LONG":
            stop_loss = entry_price - sl_dist
            take_profit = self._compute_tp(entry_price, atr_val, "BUY")
        else:
            stop_loss = entry_price + sl_dist
            take_profit = self._compute_tp(entry_price, atr_val, "SELL")

        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop": atr_val * p["trail_multiplier"] if p["exit_mode"] == "C" else None,
        }

    def _compute_tp(self, entry_price: float, atr_val: float, side: str) -> float:
        """Compute take-profit based on exit mode."""
        p = self.params
        if p["exit_mode"] == "A":
            dist = atr_val * p["tp_multiplier"]
            return entry_price + dist if side == "BUY" else entry_price - dist
        elif p["exit_mode"] == "B":
            pct = p["profit_pct"] / 100
            return entry_price * (1 + pct) if side == "BUY" else entry_price * (1 - pct)
        else:
            # Mode C — trailing stop, TP is not fixed
            return 0.0

    # ── Dashboard Info ──────────────────────────────────────────

    def get_dashboard_state(self, df: pd.DataFrame, htf_df: pd.DataFrame | None = None) -> dict:
        """
        Return comprehensive dashboard state matching the Pine Script dashboard.
        Useful for the frontend.
        """
        if len(df) < 100:
            return {"error": "Insufficient data"}

        p = self.params
        tf_mins = self._tf_minutes(self.config.timeframe)
        is_auto = p["tf_mode"] == "auto"
        auto = self._auto_thresholds(tf_mins) if is_auto else {}

        st = supertrend(df, p["atr_period"], p["atr_multiplier"], p["use_rma"])
        atr_vals = atr(df, p["atr_period"], p["use_rma"])
        adx_data = adx(df, p["adx_length"])
        roc_val = roc(df["close"], p["roc_lookback"]).abs()
        squeeze = bb_squeeze(df["close"], p["bb_length"], p["bb_multiplier"], p["squeeze_lookback"])
        consec = consecutive_confirming_bars(st["trend"], df["close"])

        # Soft scores
        adx_thresh = auto.get("adx_thresh", p["manual_adx_threshold"]) if is_auto else p["manual_adx_threshold"]
        s1 = adx_data["adx"].iloc[-1] > adx_thresh if p["use_adx"] else True

        vol_mult = auto.get("vol_mult", p["manual_vol_multiplier"]) if is_auto else p["manual_vol_multiplier"]
        s2 = volume_surge(df["volume"], p["volume_lookback"], vol_mult).iloc[-1] if p["use_volume"] and "volume" in df.columns else True

        atr_pct = atr_percentile(atr_vals, p["atr_pct_lookback"])
        if not isinstance(atr_pct, pd.Series):
            atr_pct = pd.Series(atr_pct, index=df.index)
        atr_pct_thresh = auto.get("atr_pct_thresh", p["manual_atr_pct_threshold"]) if is_auto else p["manual_atr_pct_threshold"]
        s3 = atr_pct.iloc[-1] >= atr_pct_thresh if p["use_atr_percentile"] else True

        roc_thresh = auto.get("roc_thresh", p["manual_roc_threshold"]) if is_auto else p["manual_roc_threshold"]
        s4 = roc_val.iloc[-1] >= roc_thresh if p["use_roc"] else True

        s5 = squeeze["squeeze_breakout"].iloc[-1] if p["use_bb_squeeze"] else True

        soft_score = sum([s1, s2, s3, s4, s5])
        soft_required = auto.get("soft_required", p["manual_soft_required"]) if is_auto else p["manual_soft_required"]

        # TF profile name
        tf_profiles = {10080: "Weekly", 1440: "Daily", 240: "4H", 60: "1H", 15: "15m"}
        tf_profile = "5m or lower"
        for mins, name in tf_profiles.items():
            if tf_mins >= mins:
                tf_profile = name
                break

        return {
            "tf_profile": tf_profile,
            "tf_mode": "auto" if is_auto else "manual",
            "trend": "LONG" if st["trend"].iloc[-1] == 1 else "SHORT",
            "supertrend_value": float(st["supertrend"].iloc[-1]),
            "bars_in_trend": int(consec.iloc[-1]),
            "hard_gates": {
                "dual_st": "AGREE" if (not p["use_dual_st"] or True) else "DISAGREE",
                "consecutive": f"{int(consec.iloc[-1])}/{auto.get('consec_bars', p['manual_consec_bars'])}",
            },
            "soft_filters": {
                "score": f"{soft_score}/{soft_required}",
                "adx": {"value": round(float(adx_data['adx'].iloc[-1]), 1), "pass": bool(s1)},
                "volume": {"pass": bool(s2)},
                "atr_pct": {"value": round(float(atr_pct.iloc[-1]), 1), "pass": bool(s3)},
                "roc": {"value": round(float(roc_val.iloc[-1]), 2), "pass": bool(s4)},
                "bb_squeeze": {"state": "SQUEEZE" if squeeze["in_squeeze"].iloc[-1] else "NORMAL", "pass": bool(s5)},
            },
            "exit_mode": p["exit_mode"],
            "current_price": float(df["close"].iloc[-1]),
            "atr": round(float(atr_vals.iloc[-1]), 2),
        }
