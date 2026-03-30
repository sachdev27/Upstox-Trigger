import pandas as pd
from datetime import datetime

from app.strategies.base import BaseStrategy, StrategyConfig
from app.strategies.indicators import ema, rsi, vwap, atr
from app.orders.models import TradeSignal, TransactionType

class ScalpPro(BaseStrategy):
    """
    High-speed scalping strategy targeting small points frequently.
    Uses Fast/Slow EMA crossover, anchored VWAP trend filter, and RSI momentum confirmation.
    """

    @classmethod
    def default_params(cls) -> dict:
        return {
            "fast_ema": 9,
            "slow_ema": 21,
            "rsi_period": 14,
            "rsi_buy_thresh": 50,
            "rsi_sell_thresh": 50,
            "use_vwap_filter": True,
            "atr_period": 14,
            "sl_atr_multiplier": 1.0,
            "tp_atr_multiplier": 1.5,
        }

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
            "close": df["close"]
        }
        self._last_df_id = df_id
        self._indicator_cache = res
        return res

    def get_dashboard_state(
        self, df: pd.DataFrame, htf_df: pd.DataFrame | None = None
    ) -> dict:
        """Calculate state for the UI overlay."""
        p = self.params
        if len(df) < p["slow_ema"]:
            return {}

        ind = self._get_indicators(df)
        
        # Determine current state
        close = ind["close"].iloc[-1]
        fast_curr = ind["fast"].iloc[-1]
        slow_curr = ind["slow"].iloc[-1]
        
        trend = "BULLISH" if fast_curr > slow_curr else "BEARISH"
        
        if not p["use_vwap_filter"]:
            vwap_state = "PASS"
        else:
            vwap_val = ind["vwap"].iloc[-1]
            if trend == "BULLISH" and close > vwap_val:
                vwap_state = "PASS (Above)"
            elif trend == "BEARISH" and close < vwap_val:
                vwap_state = "PASS (Below)"
            else:
                vwap_state = "FAIL"
        
        return {
            "EMA Trend": trend,
            "VWAP Filter": vwap_state,
            "RSI Value": round(ind["rsi"].iloc[-1], 2),
            "ATR": round(ind["atr"].iloc[-1], 2),
        }

    def on_candle(
        self,
        df: pd.DataFrame,
        htf_df: pd.DataFrame | None = None,
    ) -> TradeSignal | None:
        p = self.params
        if len(df) < p["slow_ema"]:
            return None

        ind = self._get_indicators(df)
        
        fast_curr = ind["fast"].iloc[-1]
        fast_prev = ind["fast"].iloc[-2]
        slow_curr = ind["slow"].iloc[-1]
        slow_prev = ind["slow"].iloc[-2]

        # EMA Crossovers
        buy_cross = fast_curr > slow_curr and fast_prev <= slow_prev
        sell_cross = fast_curr < slow_curr and fast_prev >= slow_prev

        if not buy_cross and not sell_cross:
            return None

        close = ind["close"].iloc[-1]
        
        # Long conditions
        if buy_cross:
            # Price must be > VWAP for longs
            if p["use_vwap_filter"] and close < ind["vwap"].iloc[-1]:
                return None
            # RSI must exceed threshold
            if ind["rsi"].iloc[-1] < p["rsi_buy_thresh"]:
                return None
                
            sl_dist = ind["atr"].iloc[-1] * p["sl_atr_multiplier"]
            tp_dist = ind["atr"].iloc[-1] * p["tp_atr_multiplier"]
            
            return TradeSignal(
                strategy_name=self.config.name,
                instrument_key="",  # injected by engine
                action=TransactionType.BUY,
                price=close,
                stop_loss=close - sl_dist,
                take_profit=close + tp_dist,
                confidence_score=5,
            )

        # Short conditions
        if sell_cross:
            # Price must be < VWAP for shorts
            if p["use_vwap_filter"] and close > ind["vwap"].iloc[-1]:
                return None
            # RSI must be below threshold
            if ind["rsi"].iloc[-1] > p["rsi_sell_thresh"]:
                return None
                
            sl_dist = ind["atr"].iloc[-1] * p["sl_atr_multiplier"]
            tp_dist = ind["atr"].iloc[-1] * p["tp_atr_multiplier"]
            
            return TradeSignal(
                strategy_name=self.config.name,
                instrument_key="",
                action=TransactionType.SELL,
                price=close,
                stop_loss=close + sl_dist,
                take_profit=close - tp_dist,
                confidence_score=5,
            )
            
        return None

    def compute_exit(
        self,
        position,  # type: ignore (avoiding circular import)
        df: pd.DataFrame
    ) -> TransactionType | None:
        """
        Dynamic exit logic if SL/TP are not hit first.
        For ScalpPro, we exit early if the fast EMA strongly crosses against us.
        """
        p = self.params
        if len(df) < p["slow_ema"]:
            return None

        fast_line = ema(df["close"], p["fast_ema"])
        slow_line = ema(df["close"], p["slow_ema"])
        
        fast_curr = fast_line.iloc[-1]
        slow_curr = slow_line.iloc[-1]

        if position.type == TransactionType.BUY and fast_curr < slow_curr:
            return TransactionType.SELL  # Exit Long

        if position.type == TransactionType.SELL and fast_curr > slow_curr:
            return TransactionType.BUY   # Exit Short

        return None
