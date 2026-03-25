"""
Automation Engine — the core orchestrator that connects everything.

Ties together: Market Data → Strategy Evaluation → Order Execution
This is the brain that runs the 24/7 automation loop.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

from app.config import get_settings
from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService
from app.orders.service import OrderService
from app.orders.models import TradeSignal
from app.strategies.base import BaseStrategy, StrategyConfig
from app.strategies.supertrend_pro import SuperTrendPro
from app.database.connection import get_session, TradeLog

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Strategy class registry
STRATEGY_CLASSES = {
    "SuperTrendPro": SuperTrendPro,
}


class AutomationEngine:
    """
    The main automation engine that orchestrates the trading loop.

    Lifecycle:
        1. initialize()  → auth, load strategies, download instruments
        2. run_cycle()    → called on each candle close (by scheduler)
        3. evaluate()     → run strategies against latest data
        4. execute()      → place orders for valid signals
    """

    def __init__(self):
        self.settings = get_settings()
        self._auth = get_auth_service()
        self._market_service: MarketDataService | None = None
        self._order_service: OrderService | None = None
        self._active_strategies: list[tuple[StrategyConfig, BaseStrategy]] = []
        self._signals_log: list[dict] = []
        self._trades_today: list[dict] = []
        self._daily_pnl: float = 0.0
        self._is_initialized: bool = False
        self._is_running: bool = False
        self.auto_mode: bool = False
        
        # Risk settings
        self.trading_capital: float = 100000.0
        self.risk_per_trade_pct: float = 1.0
        self.max_daily_loss_pct: float = 3.0
        self.max_open_trades: int = 3

    # ── Initialization ──────────────────────────────────────────

    def initialize(self):
        """Initialize all services and load strategies."""
        try:
            config = self._auth.get_configuration()
            self._market_service = MarketDataService(config)
            self._order_service = OrderService(config)
            self._is_initialized = True
            logger.info("✅ Automation engine initialized.")
        except Exception as e:
            logger.error(f"❌ Engine initialization failed: {e}")
            self._is_initialized = False

    def load_strategy(
        self,
        strategy_class_name: str,
        name: str,
        instruments: list[str],
        timeframe: str = "15m",
        params: dict | None = None,
        paper_trading: bool = True,
    ):
        """Register a strategy to be evaluated on each cycle."""
        cls = STRATEGY_CLASSES.get(strategy_class_name)
        if not cls:
            raise ValueError(
                f"Unknown strategy: {strategy_class_name}. "
                f"Available: {list(STRATEGY_CLASSES.keys())}"
            )

        config = StrategyConfig(
            name=name,
            enabled=True,
            instruments=instruments,
            timeframe=timeframe,
            params=params or {},
            paper_trading=paper_trading,
        )
        strategy = cls(config)
        self._active_strategies.append((config, strategy))
        logger.info(f"📊 Strategy loaded: {name} on {instruments} ({timeframe})")

    # ── Main Cycle ──────────────────────────────────────────────

    async def run_cycle(self):
        """
        Execute one strategy evaluation cycle.
        Called by the scheduler on each candle close or manually.
        """
        if not self._is_initialized:
            logger.warning("Engine not initialized — skipping cycle.")
            return

        if not self._active_strategies:
            return

        now = datetime.now(IST)
        logger.info(f"🔄 Running cycle at {now.strftime('%H:%M:%S')}")

        for config, strategy in self._active_strategies:
            if not config.enabled:
                continue

            for instrument in config.instruments:
                try:
                    signal = await self._evaluate_instrument(
                        strategy, config, instrument
                    )
                    if signal:
                        await self._handle_signal(signal, config)
                except Exception as e:
                    logger.error(
                        f"Error evaluating {instrument} with {config.name}: {e}"
                    )

    async def _evaluate_instrument(
        self,
        strategy: BaseStrategy,
        config: StrategyConfig,
        instrument_key: str,
    ) -> TradeSignal | None:
        """Fetch candle data and evaluate strategy for one instrument."""
        # Map timeframe to API interval
        tf_to_interval = {
            "1m": "1minute", "5m": "5minute", "15m": "15minute",
            "30m": "30minute", "1H": "60minute", "4H": "day", "1D": "day",
        }
        interval = tf_to_interval.get(config.timeframe, "15minute")

        # Fetch historical candles
        candles = self._market_service.get_intraday_candles(
            instrument_key, interval
        )

        if not candles or len(candles) < 100:
            logger.debug(
                f"Insufficient candle data for {instrument_key}: {len(candles)} bars"
            )
            return None

        # Build DataFrame
        df = pd.DataFrame(candles)
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Evaluate strategy
        signal = strategy.on_candle(df)
        if signal:
            signal.instrument_key = instrument_key
            self._signals_log.append({
                "timestamp": datetime.now(IST).isoformat(),
                "strategy": config.name,
                "instrument": instrument_key,
                "action": signal.action.value,
                "price": signal.price,
                "confidence": signal.confidence_score,
            })
            logger.info(
                f"🎯 Signal: {signal.action.value} {instrument_key} "
                f"@ {signal.price:.2f} (score: {signal.confidence_score})"
            )

        return signal

    async def _handle_signal(self, signal: TradeSignal, config: StrategyConfig):
        """Handle a validated trade signal — paper trade or execute."""
        # Risk check: are we at max daily loss?
        max_loss_abs = self.trading_capital * (self.max_daily_loss_pct / 100)
        if self._daily_pnl <= -max_loss_abs:
            logger.warning(
                f"🛑 MAX DAILY LOSS HIT ({-self._daily_pnl:.2f} >= {max_loss_abs:.2f}). "
                f"Blocking {signal.action.value} on {signal.instrument_key}."
            )
            # Auto-disable engine
            self.auto_mode = False
            return
            
        if config.paper_trading:
            logger.info(
                f"📝 [PAPER] {signal.action.value} {signal.instrument_key} "
                f"@ {signal.price:.2f} | SL: {signal.stop_loss:.2f} | "
                f"TP: {signal.take_profit:.2f}"
            )
            self._trades_today.append({
                "timestamp": datetime.now(IST).isoformat(),
                "type": "paper",
                "strategy": signal.strategy_name,
                "instrument": signal.instrument_key,
                "action": signal.action.value,
                "price": signal.price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "score": signal.confidence_score,
            })

            # Log to database
            try:
                session = get_session()
                log = TradeLog(
                    timestamp=datetime.now(IST),
                    strategy_name=signal.strategy_name,
                    instrument_key=signal.instrument_key,
                    action=signal.action.value,
                    quantity=0,
                    price=signal.price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    status="paper",
                    metadata_json=signal.metadata,
                )
                session.add(log)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"DB log failed: {e}")

        else:
            # Live execution
            try:
                result = self._order_service.place_signal(signal)
                logger.info(f"💰 [LIVE] Order placed: {result}")
                self._trades_today.append({
                    "timestamp": datetime.now(IST).isoformat(),
                    "type": "live",
                    "strategy": signal.strategy_name,
                    "instrument": signal.instrument_key,
                    "action": signal.action.value,
                    "price": signal.price,
                    "order_result": result,
                })
            except Exception as e:
                logger.error(f"❌ Order execution failed: {e}")

    # ── Status & Reporting ──────────────────────────────────────

    def get_status(self) -> dict:
        """Get current engine status for the dashboard."""
        return {
            "initialized": self._is_initialized,
            "running": self._is_running,
            "auto_mode": self.auto_mode,
            "risk_controls": {
                "trading_capital": self.trading_capital,
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "max_daily_loss_pct": self.max_daily_loss_pct,
                "max_open_trades": self.max_open_trades,
            },
            "daily_pnl": self._daily_pnl,
            "active_strategies": [
                {
                    "name": config.name,
                    "enabled": config.enabled,
                    "instruments": config.instruments,
                    "timeframe": config.timeframe,
                    "paper_trading": config.paper_trading,
                    "latest_metrics": getattr(strategy, "latest_metrics", None),
                }
                for config, strategy in self._active_strategies
            ],
            "signals_today": len(self._signals_log),
            "trades_today": len(self._trades_today),
            "recent_signals": self._signals_log[-10:],
            "recent_trades": self._trades_today[-10:],
            "market_hours": self._order_service.is_market_hours() if self._order_service else False,
        }

    def get_signals_log(self) -> list[dict]:
        """Get all signals generated today."""
        return self._signals_log

    def get_trades_log(self) -> list[dict]:
        """Get all trades executed today."""
        return self._trades_today

    def reset_daily(self):
        """Reset daily counters (called post-market)."""
        self._signals_log.clear()
        self._trades_today.clear()
        self._daily_pnl = 0.0
        logger.info("Daily counters reset.")


# Module-level singleton
_engine: AutomationEngine | None = None


def get_engine() -> AutomationEngine:
    """Get or create the AutomationEngine singleton."""
    global _engine
    if _engine is None:
        _engine = AutomationEngine()
    return _engine
