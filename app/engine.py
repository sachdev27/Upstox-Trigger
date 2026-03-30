"""
Automation Engine — the core orchestrator that connects everything.

Ties together: Market Data → Strategy Evaluation → Order Execution
This is the brain that runs the 24/7 automation loop.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

class UpstoxRateLimiter:
    """
    Enforces Upstox Historical API Limits:
    - 50 requests per second (using 45 for safety)
    - 500 requests per minute (using 450 for safety)
    - 2000 requests per 30 minutes (using 1900 for safety)
    """
    def __init__(self):
        self.lock = asyncio.Lock()
        self.history_sec = deque()
        self.history_min = deque()
        self.history_30min = deque()

    async def wait_for_token(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                
                # Cleanup old requests
                while self.history_sec and now - self.history_sec[0] > 1.0:
                    self.history_sec.popleft()
                while self.history_min and now - self.history_min[0] > 60.0:
                    self.history_min.popleft()
                while self.history_30min and now - self.history_30min[0] > 1800.0:
                    self.history_30min.popleft()

                # Check limits
                if len(self.history_sec) >= 45:
                    await asyncio.sleep(1.0 - (now - self.history_sec[0]) + 0.01)
                    continue
                if len(self.history_min) >= 450:
                    await asyncio.sleep(60.0 - (now - self.history_min[0]) + 0.1)
                    continue
                if len(self.history_30min) >= 1900:
                    await asyncio.sleep(1800.0 - (now - self.history_30min[0]) + 1.0)
                    continue

                # Consume token
                self.history_sec.append(now)
                self.history_min.append(now)
                self.history_30min.append(now)
                break

from app.config import get_settings
from app.auth.service import get_auth_service
from app.market_data.service import MarketDataService
from app.orders.service import OrderService
from app.orders.models import TradeSignal
from app.strategies.base import BaseStrategy, StrategyConfig
from app.strategies.supertrend_pro import SuperTrendPro
from app.strategies.scalp_pro import ScalpPro
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
    "ScalpPro": ScalpPro,
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

        # Rate Limiter for Upstox API
        self.rate_limiter = UpstoxRateLimiter()

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
        if self._is_initialized:
            return
        
        logger.info("🚀 Initializing Automation Engine...")
        self.sync_from_settings()

        # Auto-load last active strategy if none present
        if not self._active_strategies:
            last_class = self.settings.ACTIVE_STRATEGY_CLASS
            last_name = self.settings.ACTIVE_STRATEGY_NAME
            if last_class and last_name:
                logger.info(f"🔄 Auto-loading last active strategy: {last_name}")
                try:
                    self.load_strategy(
                        strategy_class_name=last_class,
                        name=last_name,
                        instruments=[self.settings.NIFTY], # Default if none
                        timeframe="15m" # Default if none
                    )
                except Exception as e:
                    logger.error(f"Failed to auto-load strategy: {e}")
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

    async def _process_instrument_tf(self, strategy: BaseStrategy, tf_config: StrategyConfig, target: str):
        """Helper to evaluate a single instrument and timeframe configuration."""
        try:
            signal = await self._evaluate_instrument(strategy, tf_config, target)
            if signal:
                await self._handle_signal(signal, tf_config)
        except Exception as e:
            logger.error(f"Error evaluating {target} ({tf_config.timeframe}) with {tf_config.name}: {e}")

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
                tf_overrides = {}  # instrument_key -> [timeframes]
                if instrument == "NIFTY200":
                    from app.monitoring.routes import get_nifty200_list
                    target_instruments = get_nifty200_list()
                elif instrument == "CUSTOM_WATCHLIST":
                    from app.database.connection import get_session, Watchlist
                    session = get_session()
                    wl_items = session.query(Watchlist).all()
                    target_instruments = [w.instrument_key for w in wl_items]
                    # Build TF override map from watchlist
                    for w in wl_items:
                        if w.timeframes:
                            tf_overrides[w.instrument_key] = w.timeframes
                    session.close()

                total_scans = sum(len(tf_overrides.get(t, [config.timeframe])) for t in target_instruments)
                logger.info(f"🔍 Scanning {len(target_instruments)} instruments ({total_scans} timeframe combinations)...")

                tasks = []
                for target in target_instruments:
                    # Get timeframes for this instrument (custom or default)
                    timeframes_to_scan = tf_overrides.get(target, [config.timeframe])
                    
                    for tf in timeframes_to_scan:
                        # Create a shallow copy of config with this TF
                        tf_config = StrategyConfig(
                            name=config.name,
                            enabled=config.enabled,
                            instruments=config.instruments,
                            timeframe=tf,
                            params=config.params,
                            paper_trading=config.paper_trading,
                        )
                        tasks.append(self._process_instrument_tf(strategy, tf_config, target))
                
                if tasks:
                    # Run all evaluations for this strategy/instrument group concurrently
                    await asyncio.gather(*tasks)

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
            # Fallbacks for literal UI interval strings
            "1minute": "1minute", "5minute": "5minute", "15minute": "15minute",
            "30minute": "30minute", "1hour": "60minute", "day": "day"
        }
        interval = tf_to_interval.get(config.timeframe, "15minute")

        # 1. Prepare Fetch Tasks
        tasks = [
            asyncio.to_thread(self._market_service.get_intraday_candles, instrument_key, interval)
        ]
        
        has_htf = strategy.params.get("use_htf_filter")
        if has_htf:
            htf_tf = strategy.params.get("htf_timeframe", "1D")
            htf_interval = "day" if htf_tf in ["1D", "D", "W", "1W"] else "60minute" if htf_tf in ["1H", "60m"] else "day"
            tasks.append(asyncio.to_thread(self._market_service.get_historical_candles, instrument_key, htf_interval))

        # 2. Wait for Rate Limit (consume tokens for all tasks in this evaluation)
        for _ in range(len(tasks)):
            await self.rate_limiter.wait_for_token()

        # 3. Execute Fetching Concurrently
        results = await asyncio.gather(*tasks)
        candles = results[0]
        htf_candles = results[1] if (has_htf and len(results) > 1) else None

        if not candles or len(candles) < 100:
            logger.debug(
                f"Insufficient candle data for {instrument_key}: {len(candles)} bars"
            )
            return None

        # Build DataFrames
        df = pd.DataFrame(candles)
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        htf_df = None
        if htf_candles:
            htf_df = pd.DataFrame(htf_candles)
            if "datetime" in htf_df.columns:
                htf_df["datetime"] = pd.to_datetime(htf_df["datetime"])
            for col in ["open", "high", "low", "close", "volume"]:
                if col in htf_df.columns:
                    htf_df[col] = pd.to_numeric(htf_df[col], errors="coerce")

        # Evaluate strategy
        if hasattr(strategy, 'get_dashboard_state'):
            strategy.latest_metrics = strategy.get_dashboard_state(df, htf_df=htf_df)
            
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
                        "confidence": signal.confidence_score,
                        "latest_metrics": getattr(strategy, "latest_metrics", {})
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
            "active_strategy_class": self.settings.ACTIVE_STRATEGY_CLASS,
            "active_strategy_name": self.settings.ACTIVE_STRATEGY_NAME,
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
            "active_signals_count": self._get_active_signal_count(),
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

    def _get_active_signal_count(self) -> int:
        """Get count of active (non-closed) signals from DB."""
        try:
            from app.database.connection import get_session, ActiveSignal
            session = get_session()
            count = session.query(ActiveSignal).filter_by(status="active").count()
            session.close()
            return count
        except Exception:
            return 0

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
