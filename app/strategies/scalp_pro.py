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

    def get_dashboard_state(
        self, df: pd.DataFrame, htf_df: pd.DataFrame | None = None
    ) -> dict:
        """Calculate state for the UI overlay."""
        p = self.params
        if len(df) < p["slow_ema"]:
            return {}

        fast_line = ema(df["close"], p["fast_ema"])
        slow_line = ema(df["close"], p["slow_ema"])
        rsi_line = rsi(df["close"], p["rsi_period"])
        vwap_line = vwap(df) if p["use_vwap_filter"] else pd.Series(0, index=df.index)
        atr_val = atr(df, p["atr_period"]).iloc[-1]

        # Determine current state
        close = df["close"].iloc[-1]
        vwap_val = vwap_line.iloc[-1]
        
        trend = "BULLISH" if fast_line.iloc[-1] > slow_line.iloc[-1] else "BEARISH"
        
        if not p["use_vwap_filter"]:
            vwap_state = "PASS"
        else:
            if trend == "BULLISH" and close > vwap_val:
                vwap_state = "PASS (Above)"
            elif trend == "BEARISH" and close < vwap_val:
                vwap_state = "PASS (Below)"
            else:
                vwap_state = "FAIL"
        
        return {
            "EMA Trend": trend,
            "VWAP Filter": vwap_state,
            "RSI Value": round(rsi_line.iloc[-1], 2),
            "ATR": round(atr_val, 2),
        }


    def on_candle(
        self,
        df: pd.DataFrame,
        htf_df: pd.DataFrame | None = None,
    ) -> TradeSignal | None:
        p = self.params
        if len(df) < p["slow_ema"]:
            return None

        # Calculate indicators
        fast_line = ema(df["close"], p["fast_ema"])
        slow_line = ema(df["close"], p["slow_ema"])
        
        fast_curr = fast_line.iloc[-1]
        fast_prev = fast_line.iloc[-2]
        slow_curr = slow_line.iloc[-1]
        slow_prev = slow_line.iloc[-2]

        # EMA Crossovers
        buy_cross = fast_curr > slow_curr and fast_prev <= slow_prev
        sell_cross = fast_curr < slow_curr and fast_prev >= slow_prev

        if not buy_cross and not sell_cross:
            return None

        rsi_line = rsi(df["close"], p["rsi_period"])
        vwap_line = vwap(df) if p["use_vwap_filter"] else None
        close = df["close"].iloc[-1]
        
        # Long conditions
        if buy_cross:
            # Price must be > VWAP for longs
            if p["use_vwap_filter"] and close < vwap_line.iloc[-1]:
                return None
            # RSI must exceed threshold
            if rsi_line.iloc[-1] < p["rsi_buy_thresh"]:
                return None
                
            atr_val = atr(df, p["atr_period"]).iloc[-1]
            sl_dist = atr_val * p["sl_atr_multiplier"]
            tp_dist = atr_val * p["tp_atr_multiplier"]
            
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
            if p["use_vwap_filter"] and close > vwap_line.iloc[-1]:
                return None
            # RSI must be below threshold
            if rsi_line.iloc[-1] > p["rsi_sell_thresh"]:
                return None
                
            atr_val = atr(df, p["atr_period"]).iloc[-1]
            sl_dist = atr_val * p["sl_atr_multiplier"]
            tp_dist = atr_val * p["tp_atr_multiplier"]
            
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
