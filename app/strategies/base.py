"""
Base Strategy — abstract interface that all strategies must implement.

Any new strategy just subclasses BaseStrategy and implements:
  - on_candle()    → evaluate new data, return a signal or None
  - compute_exit() → determine exit levels for open positions
  - default_params() → define configurable parameters
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.orders.models import TradeSignal


@dataclass
class StrategyConfig:
    """Runtime configuration for a strategy instance."""

    name: str
    enabled: bool = True
    instruments: list[str] = field(default_factory=list)
    timeframe: str = "15m"             # "1m", "5m", "15m", "1H", "4H", "1D"
    params: dict[str, Any] = field(default_factory=dict)
    paper_trading: bool = True          # False = live execution


class BaseStrategy(ABC):
    """
    Abstract base for all trading strategies.

    Lifecycle:
        1. __init__(config) — load parameters
        2. on_candle(df) — called on each new candle close
        3. compute_exit(position, current_price) — called each tick while in position
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.name = config.name
        self.params = {**self.default_params(), **config.params}
        self.latest_metrics: dict[str, Any] = {}

    @abstractmethod
    def on_candle(self, df: pd.DataFrame) -> TradeSignal | None:
        """
        Evaluate the latest candle data and return a trade signal.

        Args:
            df: DataFrame with columns [datetime, open, high, low, close, volume]
                Sorted oldest→newest. Latest candle is df.iloc[-1].

        Returns:
            TradeSignal if a valid entry signal is generated, else None.
        """
        ...

    @abstractmethod
    def compute_exit(
        self,
        entry_price: float,
        position_side: str,  # "LONG" or "SHORT"
        current_price: float,
        df: pd.DataFrame,
    ) -> dict:
        """
        Compute exit levels for an open position.

        Returns:
            dict with keys: stop_loss, take_profit, trailing_stop (any may be None)
        """
        ...

    @staticmethod
    @abstractmethod
    def default_params() -> dict[str, Any]:
        """
        Return the default parameter dict for this strategy.
        These can be overridden via StrategyConfig.params.

        Example:
            return {
                "atr_period": 10,
                "atr_multiplier": 3.0,
                "use_dual_st": True,
                ...
            }
        """
        ...

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a strategy parameter by name."""
        return self.params.get(key, default)

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name!r} tf={self.config.timeframe}>"
