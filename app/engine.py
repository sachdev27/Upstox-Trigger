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

from app.engine_pipeline import (
    RiskGuardProcessor, ATMResolverProcessor,
    ExecutionProcessor, AlerterProcessor, BroadcastProcessor
)

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
        
        # WebSocket broadcast callback (set by main.py)
        self.broadcast_callback = None

        # Configuration (synced later)
        self.paper_trading: bool = True
        self.trading_side: str = "BOTH"
        self.trading_capital: float = 100000.0
        self.risk_per_trade_pct: float = 1.0
        self.max_daily_loss_pct: float = 3.0
        self.max_open_trades: int = 3

        # --- Signal Processing Pipeline ---
        self._pipeline = [
            RiskGuardProcessor(),
            ATMResolverProcessor(),
            ExecutionProcessor(),
            AlerterProcessor(),
            BroadcastProcessor()
        ]

        # Load config from DB-backed settings
        self.sync_from_settings()

    def sync_from_settings(self):
        """Sync engine runtime config from the DB-backed Settings singleton."""
        s = self.settings
        s.load_from_db()
        self.paper_trading = s.PAPER_TRADING
        self.trading_side = s.TRADING_SIDE
        self.trading_capital = s.TRADING_CAPITAL
        self.risk_per_trade_pct = s.MAX_RISK_PER_TRADE_PCT
        self.max_daily_loss_pct = s.MAX_DAILY_LOSS_PCT
        self.max_open_trades = s.MAX_OPEN_TRADES

    # ── Initialization ──────────────────────────────────────────

    def initialize(self):
        """Initialize all services and load strategies."""
        try:
            # Refresh config from DB
            self.sync_from_settings()

            try:
                # 1. Market Data ALWAYS uses Live configuration (Sandbox doesn't support market data)
                live_config = self._auth.get_configuration(use_sandbox=False)
                self._market_service = MarketDataService(live_config)
            except Exception as e:
                logger.error(f"⚠️ Market Data initialization failed: {e}")
                logger.info("💡 Please log in with Upstox LIVE to enable strategy feedback.")
                self._market_service = None

            # 2. Order Service moves between Live/Sandbox based on global flag
            try:
                order_config = self._auth.get_configuration(use_sandbox=self.settings.USE_SANDBOX)
                self._order_service = OrderService(order_config)
            except Exception as e:
                logger.error(f"⚠️ Order Service initialization failed: {e}")
                self._order_service = None
            
            self._is_initialized = True
            logger.info(f"✅ Automation engine initialized ({'SANDBOX' if self.settings.USE_SANDBOX else 'LIVE'} mode).")
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
                # Expanded watchlist support
                target_instruments = [instrument]
                if instrument == "NIFTY200":
                    from app.monitoring.routes import get_nifty200_list
                    target_instruments = get_nifty200_list()
                elif instrument == "CUSTOM_WATCHLIST":
                    from app.database.connection import get_session, Watchlist
                    session = get_session()
                    target_instruments = [w.instrument_key for w in session.query(Watchlist).all()]
                    session.close()

                for target in target_instruments:
                    try:
                        signal = await self._evaluate_instrument(
                            strategy, config, target
                        )
                        if signal:
                            await self._handle_signal(signal, config)
                    except Exception as e:
                        logger.error(
                            f"Error evaluating {target} with {config.name}: {e}"
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

        # Fetch HTF candles if requested by strategy
        htf_df = None
        if strategy.params.get("use_htf_filter"):
            htf_tf = strategy.params.get("htf_timeframe", "1D")
            # Map simplified TF to Upstox API intervals
            htf_interval = "day" if htf_tf in ["1D", "D", "W", "1W"] else "60minute" if htf_tf in ["1H", "60m"] else "day"
            
            htf_candles = self._market_service.get_historical_candles(
                instrument_key, htf_interval
            )
            if htf_candles:
                htf_df = pd.DataFrame(htf_candles)
                if "datetime" in htf_df.columns:
                    htf_df["datetime"] = pd.to_datetime(htf_df["datetime"])
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in htf_df.columns:
                        htf_df[col] = pd.to_numeric(htf_df[col], errors="coerce")

        # Evaluate strategy
        signal = strategy.on_candle(df, htf_df=htf_df)
        if signal:
            signal.instrument_key = instrument_key
            self._signals_log.append({
                "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
                "strategy": config.name,
                "strategy_name": config.name,
                "instrument": instrument_key,
                "instrument_key": instrument_key,
                "action": signal.action.value,
                "price": signal.price,
                "confidence": signal.confidence_score,
            })
            logger.info(
                f"🎯 SIGNAL: {signal.action.value} {instrument_key} @ {signal.price:.2f}"
            )
            
            # Broadcast signal to UI
            if self.broadcast_callback:
                asyncio.create_task(self.broadcast_callback({
                    "type": "new_signal",
                    "data": {
                        "timestamp": datetime.now(IST).isoformat(),
                        "strategy": config.name,
                        "instrument": instrument_key,
                        "action": signal.action.value,
                        "price": signal.price,
                        "confidence": signal.confidence_score
                    }
                }))

        return signal

    async def _handle_signal(self, signal: TradeSignal, config: StrategyConfig):
        """Handle a validated trade signal via the processing pipeline."""
        for processor in self._pipeline:
            try:
                should_continue = await processor.process(signal, config, self)
                if not should_continue:
                    break
            except Exception as e:
                logger.error(f"Error in pipeline processor {processor.__class__.__name__}: {e}")
                break

    # ── Status & Reporting ──────────────────────────────────────

    def get_status(self) -> dict:
        """Get current engine status for the dashboard."""
        return {
            "initialized": self._is_initialized,
            "running": self._is_running,
            "auto_mode": self.auto_mode,
            "paper_trading": self.paper_trading,
            "trading_side": self.trading_side,
            "risk_controls": {
                "trading_capital": self.trading_capital,
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "max_daily_loss_pct": self.max_daily_loss_pct,
                "max_open_trades": self.max_open_trades,
            },
            "daily_pnl": self._daily_pnl,
            "strategy_hud": (self._active_strategies[0][1].latest_metrics if self._active_strategies and hasattr(self._active_strategies[0][1], 'latest_metrics') else {}) or {},
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

    async def trigger_test_signal(self, instrument_key: str) -> dict:
        """Force a test signal for debugging."""
        logger.info(f"🧪 [TEST] Triggering manual signal for {instrument_key}")
        
        # Create a fake signal
        from app.orders.models import TransactionType
        signal = TradeSignal(
            strategy_name="Manual Test",
            instrument_key=instrument_key,
            action=TransactionType.BUY,
            price=25000.0,  # Arbitrary for test
            stop_loss=24900.0,
            take_profit=25300.0,
            confidence_score=5,
        )
        
        # Use a dummy strategy config
        dummy_config = StrategyConfig(name="Test", enabled=True, instruments=[instrument_key], timeframe="1m", paper_trading=True)
        
        await self._handle_signal(signal, dummy_config)
        return {"action": signal.action.value, "instrument": instrument_key}

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
