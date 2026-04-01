"""Trading strategies — base class, indicators, and strategy implementations."""

from .base import BaseStrategy, StrategyConfig
from .supertrend_pro import SuperTrendPro
from .scalp_pro import ScalpPro

__all__ = ["BaseStrategy", "StrategyConfig", "SuperTrendPro", "ScalpPro"]
