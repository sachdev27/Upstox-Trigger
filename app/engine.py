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

# ── Nifty 100 + 500 Instrument Keys ────────────────────────────
# Sourced from ind_nifty500list.csv ↔ instrument_list.csv cross-reference.
# These power the NIFTY100 / NIFTY500 watchlist keywords in run_cycle().

NIFTY100_KEYS: list[str] = [
    "NSE_EQ|INE769A01020","NSE_EQ|INE117A01022","NSE_EQ|INE358A01014",
    "NSE_EQ|INE674K01013","NSE_EQ|INE647O01011","NSE_EQ|INE404A01024",
    "NSE_EQ|INE012A01025","NSE_EQ|INE423A01024","NSE_EQ|INE364U01010",
    "NSE_EQ|INE742F01042","NSE_EQ|INE079A01024","NSE_EQ|INE437A01024",
    "NSE_EQ|INE021A01026","NSE_EQ|INE006I01046","NSE_EQ|INE949L01017",
    "NSE_EQ|INE238A01034","NSE_EQ|INE917I01010","NSE_EQ|INE918I01026",
    "NSE_EQ|INE397D01024","NSE_EQ|INE376G01013","NSE_EQ|INE216A01030",
    "NSE_EQ|INE059A01026","NSE_EQ|INE522F01014","NSE_EQ|INE259A01022",
    "NSE_EQ|INE016A01026","NSE_EQ|INE361B01024","NSE_EQ|INE935N01020",
    "NSE_EQ|INE066A01021","NSE_EQ|INE102D01028","NSE_EQ|INE047A01021",
    "NSE_EQ|INE176B01034","NSE_EQ|INE860A01027","NSE_EQ|INE040A01034",
    "NSE_EQ|INE795G01014","NSE_EQ|INE158A01026","NSE_EQ|INE038A01020",
    "NSE_EQ|INE030A01027","NSE_EQ|INE090A01021","NSE_EQ|INE095A01012",
    "NSE_EQ|INE335Y01020","NSE_EQ|INE154A01025","NSE_EQ|INE019A01038",
    "NSE_EQ|INE018A01030","NSE_EQ|INE326A01037","NSE_EQ|INE101A01026",
    "NSE_EQ|INE196A01026","NSE_EQ|INE585B01010","NSE_EQ|INE414G01012",
    "NSE_EQ|INE239A01024","NSE_EQ|INE733E01010","NSE_EQ|INE213A01029",
    "NSE_EQ|INE761H01022","NSE_EQ|INE318A01026","NSE_EQ|INE455K01017",
    "NSE_EQ|INE752E01010","NSE_EQ|INE002A01018","NSE_EQ|INE123W01016",
    "NSE_EQ|INE062A01020","NSE_EQ|INE070A01015","NSE_EQ|INE044A01036",
    "NSE_EQ|INE192A01025","NSE_EQ|INE081A01020","NSE_EQ|INE467B01029",
    "NSE_EQ|INE669C01036","NSE_EQ|INE280A01028","NSE_EQ|INE685A01028",
    "NSE_EQ|INE481G01011","NSE_EQ|INE205A01025","NSE_EQ|INE075A01022",
]

NIFTY500_KEYS: list[str] = []   # populated lazily from CSV on first access

def _load_nifty500_keys() -> list[str]:
    """Read Nifty-500 instrument keys from CSV files (lazy, called once)."""
    import csv as _csv
    from pathlib import Path as _Path
    root = _Path(__file__).parent.parent
    n500_csv = root / "ind_nifty500list.csv"
    inst_csv = root / "instrument_list.csv"
    if not n500_csv.exists() or not inst_csv.exists():
        logger.warning("Nifty-500 CSV files not found; NIFTY500 watchlist will be empty.")
        return []
    isin_map: dict[str, str] = {}
    with open(n500_csv) as f:
        for row in _csv.DictReader(f):
            isin_map[row["ISIN Code"]] = row["Symbol"]
    keys = []
    with open(inst_csv) as f:
        for row in _csv.DictReader(f):
            k = row.get("instrument_key", "")
            isin = k.split("|")[-1] if "|" in k else ""
            if isin in isin_map and row.get("exchange") == "NSE_EQ":
                keys.append(k)
    keys.sort()
    return keys

# Strategy class registry
STRATEGY_CLASSES = {
    "SuperTrendPro": SuperTrendPro,
    "ScalpPro": ScalpPro,
}

# Nifty 200 instrument keys (used when instrument == "NIFTY200")
# Kept here to avoid coupling the engine to monitoring routes.
NIFTY200_KEYS = [
    "NSE_EQ|INE002A01018", "NSE_EQ|INE040A01034", "NSE_EQ|INE009A01021",
    "NSE_EQ|INE062A01020", "NSE_EQ|INE030A01027", "NSE_EQ|INE467B01029",
    "NSE_EQ|INE075A01022", "NSE_EQ|INE154A01025", "NSE_EQ|INE238A01034",
    "NSE_EQ|INE081A01012",
]


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
        self._paper_positions: dict[str, float] = {}  # instrument_key -> entry_price (paper trading only)
        self._last_evaluated_bar: dict[tuple[str, str, str], str] = {}
        self._managed_positions: dict[str, dict] = {}  # instrument_key -> autonomous exit state
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
        replace_existing: bool = True,
    ):
        """Register a strategy to be evaluated on each cycle."""
        cls = STRATEGY_CLASSES.get(strategy_class_name)
        if not cls:
            raise ValueError(
                f"Unknown strategy: {strategy_class_name}. "
                f"Available: {list(STRATEGY_CLASSES.keys())}"
            )

        if replace_existing:
            # Keep a single active instance per strategy class to avoid stale/duplicate evaluators.
            self._active_strategies = [
                (cfg, strat) for (cfg, strat) in self._active_strategies
                if strat.__class__.__name__ != strategy_class_name
            ]

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

    async def _process_instrument_tf(self, strategy: BaseStrategy, tf_config: StrategyConfig, target: str) -> tuple[TradeSignal, StrategyConfig] | None:
        """Evaluate one instrument/timeframe and return candidate signal for ranking."""
        try:
            signal = await self._evaluate_instrument(strategy, tf_config, target)
            if signal:
                return signal, tf_config
        except Exception as e:
            logger.error(f"Error evaluating {target} ({tf_config.timeframe}) with {tf_config.name}: {e}")
        return None

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

        # Autonomous position supervision on every cycle tick.
        if self._managed_positions:
            await self._manage_open_positions()

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
                    target_instruments = list(NIFTY200_KEYS)
                elif instrument == "NIFTY100":
                    target_instruments = list(NIFTY100_KEYS)
                elif instrument == "NIFTY500":
                    global NIFTY500_KEYS
                    if not NIFTY500_KEYS:
                        NIFTY500_KEYS = _load_nifty500_keys()
                    target_instruments = list(NIFTY500_KEYS)
                elif instrument == "CUSTOM_WATCHLIST":
                    from app.database.connection import get_session, Watchlist
                    session = get_session()
                    try:
                        wl_items = session.query(Watchlist).all()
                        target_instruments = [w.instrument_key for w in wl_items]
                        # Build TF override map from watchlist
                        for w in wl_items:
                            if w.timeframes:
                                tf_overrides[w.instrument_key] = w.timeframes
                    finally:
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
                    # Run all evaluations for this strategy/instrument group concurrently.
                    candidates = [c for c in await asyncio.gather(*tasks) if c is not None]
                    if not candidates:
                        continue

                    # Keep the highest-scored candidate per instrument to avoid overtrading duplicates.
                    by_instrument: dict[str, tuple[TradeSignal, StrategyConfig]] = {}
                    for signal, tf_config in candidates:
                        existing = by_instrument.get(signal.instrument_key)
                        if (existing is None) or (signal.confidence_score > existing[0].confidence_score):
                            by_instrument[signal.instrument_key] = (signal, tf_config)

                    unique_candidates = list(by_instrument.values())

                    params = config.params or {}
                    min_score = int(params.get("min_confidence_score", 60))
                    top_n = max(1, int(params.get("top_n_signals_per_cycle", 2)))

                    eligible = [c for c in unique_candidates if int(c[0].confidence_score or 0) >= min_score]
                    eligible.sort(key=lambda c: int(c[0].confidence_score or 0), reverse=True)
                    selected = eligible[:top_n]

                    skipped = len(unique_candidates) - len(selected)
                    if skipped > 0:
                        logger.info(
                            f"⏭️ Selectivity filter kept {len(selected)}/{len(unique_candidates)} signals "
                            f"(min_score={min_score}, top_n={top_n})"
                        )

                    for signal, tf_config in selected:
                        await self._handle_signal(signal, tf_config)

    async def _manage_open_positions(self):
        """
        Tick-level position supervisor.  Runs every 15s cycle.

        Supports:
        - Simple full-exit on TP / SL hit
        - Partial booking (TP1 → book tp1_pct%, move SL to breakeven;
                           TP2 → book tp2_pct%;
                           TP3 / trail → exit remainder)
        - Trailing SL (highest_price tracking)
        - Swarm positions (multiple lots sharing same position key prefix)
        """
        if not self._market_service:
            return

        for instrument_key, pos in list(self._managed_positions.items()):
            try:
                ltp = await asyncio.to_thread(self._market_service.get_ltp, instrument_key)
                if ltp is None:
                    continue
                ltp = float(ltp)

                entry           = float(pos.get("entry_price", 0.0))
                stop_loss       = float(pos.get("stop_loss") or 0.0)
                take_profit     = float(pos.get("take_profit") or 0.0)
                qty_remaining   = int(pos.get("quantity_remaining", pos.get("quantity") or 1))
                qty_original    = int(pos.get("quantity") or 1)
                is_paper        = bool(pos.get("is_paper", True))
                strat_name      = pos.get("strategy_name", "")

                # ── Trailing SL ──────────────────────────────────
                highest = float(pos.get("highest_price") or entry)
                if ltp > highest:
                    highest = ltp
                    pos["highest_price"] = highest

                trailing_enabled = bool(pos.get("trailing_enabled", False))
                trail_distance   = float(pos.get("trail_distance") or 0.0)
                effective_sl     = stop_loss
                if trailing_enabled and trail_distance > 0 and highest > entry:
                    tr_sl = highest - trail_distance
                    effective_sl = max(effective_sl, tr_sl)
                    pos["effective_sl"] = effective_sl   # persist updated trail

                # ── Partial Booking Levels ───────────────────────
                partial_enabled = bool(pos.get("partial_tp_enabled", False))
                tp1  = float(pos.get("tp1") or 0.0)
                tp2  = float(pos.get("tp2") or 0.0)
                tp1_pct = int(pos.get("tp1_book_pct", 40))
                tp2_pct = int(pos.get("tp2_book_pct", 40))
                tp1_booked = bool(pos.get("tp1_booked", False))
                tp2_booked = bool(pos.get("tp2_booked", False))

                # ── Check TP1 partial exit ───────────────────────
                if partial_enabled and tp1 > 0 and not tp1_booked and ltp >= tp1:
                    book_qty = max(1, round(qty_original * tp1_pct / 100))
                    book_qty = min(book_qty, qty_remaining)
                    if book_qty > 0:
                        await self._execute_partial_exit(
                            instrument_key, pos, ltp, book_qty, "TP1", is_paper
                        )
                        qty_remaining -= book_qty
                        pos["quantity_remaining"] = qty_remaining
                        pos["tp1_booked"] = True
                        # Move SL to breakeven after TP1
                        pos["stop_loss"] = entry
                        pos["effective_sl"] = entry
                        effective_sl = entry
                        logger.info(f"📈 TP1 partial exit: sold {book_qty} of {instrument_key} @ {ltp:.2f}")

                # ── Check TP2 partial exit ───────────────────────
                if partial_enabled and tp2 > 0 and tp1_booked and not tp2_booked and ltp >= tp2:
                    book_qty = max(1, round(qty_original * tp2_pct / 100))
                    book_qty = min(book_qty, qty_remaining)
                    if book_qty > 0:
                        await self._execute_partial_exit(
                            instrument_key, pos, ltp, book_qty, "TP2", is_paper
                        )
                        qty_remaining -= book_qty
                        pos["quantity_remaining"] = qty_remaining
                        pos["tp2_booked"] = True
                        logger.info(f"📈 TP2 partial exit: sold {book_qty} of {instrument_key} @ {ltp:.2f}")

                # ── Check full exit (TP / SL / trail) ───────────
                if qty_remaining <= 0:
                    # All lots booked via partial exits — clean up
                    self._managed_positions.pop(instrument_key, None)
                    self._close_active_signal_record(instrument_key)
                    continue

                hit_tp = take_profit > 0 and ltp >= take_profit
                hit_sl = effective_sl > 0 and ltp <= effective_sl

                if not (hit_tp or hit_sl):
                    continue

                exit_reason = "TP" if hit_tp else "SL/TRAIL"
                logger.info(
                    f"🛎️ Exit ({exit_reason}) {instrument_key}: "
                    f"LTP={ltp:.2f} qty={qty_remaining}"
                )

                # Live order
                if not is_paper and self._order_service:
                    from app.orders.models import OrderRequest, OrderType, ProductType, TransactionType
                    await asyncio.to_thread(
                        self._order_service.place_order,
                        OrderRequest(
                            instrument_token=instrument_key,
                            quantity=max(1, qty_remaining),
                            transaction_type=TransactionType.SELL,
                            order_type=OrderType.MARKET,
                            product=ProductType.INTRADAY,
                            tag=f"auto-exit-{strat_name}",
                        )
                    )

                pnl = (ltp - entry) * max(1, qty_remaining)
                # Add PnL from any partial exits already recorded
                pnl += float(pos.get("partial_pnl", 0.0))
                self._daily_pnl += (ltp - entry) * max(1, qty_remaining)
                self._trades_today.append({
                    "timestamp": datetime.now(IST).isoformat(),
                    "type": "paper" if is_paper else "live",
                    "strategy": strat_name,
                    "instrument": instrument_key,
                    "action": "SELL",
                    "price": ltp,
                    "reason": exit_reason,
                    "pnl": pnl,
                })

                self._close_active_signal_record(instrument_key)

                if self.broadcast_callback:
                    asyncio.create_task(self.broadcast_callback({
                        "type": "trade_executed",
                        "data": {
                            "type": "paper" if is_paper else "live",
                            "strategy": strat_name,
                            "instrument": instrument_key,
                            "action": "SELL",
                            "price": ltp,
                            "exit_reason": exit_reason,
                            "pnl": pnl,
                        }
                    }))

                self._managed_positions.pop(instrument_key, None)
            except Exception as e:
                logger.error(f"Error managing position {instrument_key}: {e}")

    async def _execute_partial_exit(
        self,
        instrument_key: str,
        pos: dict,
        ltp: float,
        qty: int,
        label: str,
        is_paper: bool,
    ):
        """Place partial exit order (paper log or live) and record PnL."""
        entry = float(pos.get("entry_price", 0.0))
        strat_name = pos.get("strategy_name", "")

        # Live market order
        if not is_paper and self._order_service:
            try:
                from app.orders.models import OrderRequest, OrderType, ProductType, TransactionType
                await asyncio.to_thread(
                    self._order_service.place_order,
                    OrderRequest(
                        instrument_token=instrument_key,
                        quantity=max(1, qty),
                        transaction_type=TransactionType.SELL,
                        order_type=OrderType.MARKET,
                        product=ProductType.INTRADAY,
                        tag=f"partial-{label.lower()}-{strat_name}",
                    )
                )
            except Exception as e:
                logger.error(f"Partial exit order failed ({label}): {e}")

        partial_pnl = (ltp - entry) * qty
        pos["partial_pnl"] = float(pos.get("partial_pnl", 0.0)) + partial_pnl
        self._daily_pnl += partial_pnl

        self._trades_today.append({
            "timestamp": datetime.now(IST).isoformat(),
            "type": "paper" if is_paper else "live",
            "strategy": strat_name,
            "instrument": instrument_key,
            "action": f"PARTIAL-SELL ({label})",
            "price": ltp,
            "qty": qty,
            "pnl": partial_pnl,
        })

        if self.broadcast_callback:
            asyncio.create_task(self.broadcast_callback({
                "type": "trade_executed",
                "data": {
                    "type": "paper" if is_paper else "live",
                    "strategy": strat_name,
                    "instrument": instrument_key,
                    "action": f"PARTIAL-SELL ({label})",
                    "price": ltp,
                    "pnl": partial_pnl,
                }
            }))

    def _close_active_signal_record(self, instrument_key: str):
        """Mark latest active signal as closed once autonomous exit is executed."""
        try:
            from app.database.connection import get_session, ActiveSignal
            session = get_session()
            try:
                sig = (
                    session.query(ActiveSignal)
                    .filter(
                        ActiveSignal.instrument_key == instrument_key,
                        ActiveSignal.status == "active",
                    )
                    .order_by(ActiveSignal.created_at.desc())
                    .first()
                )
                if sig:
                    sig.status = "closed"
                    sig.closed_at = datetime.now(timezone.utc)
                    session.commit()
            finally:
                session.close()
        except Exception:
            pass

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

        # Deduplicate per-candle evaluation: with a fast scheduler (e.g., every 15s),
        # candle-based strategies must not be re-evaluated on the same bar.
        bar_ts = None
        bar_key = None
        if "datetime" in df.columns:
            bar_ts = df["datetime"].iloc[-1]
        elif "time" in df.columns:
            bar_ts = df["time"].iloc[-1]

        if bar_ts is not None:
            eval_key = (config.name, instrument_key, config.timeframe)
            bar_key = str(bar_ts)
            if self._last_evaluated_bar.get(eval_key) == bar_key:
                return None
            self._last_evaluated_bar[eval_key] = bar_key

        htf_df = None
        if htf_candles:
            htf_df = pd.DataFrame(htf_candles)
            if "datetime" in htf_df.columns:
                htf_df["datetime"] = pd.to_datetime(htf_df["datetime"])
            for col in ["open", "high", "low", "close", "volume"]:
                if col in htf_df.columns:
                    htf_df[col] = pd.to_numeric(htf_df[col], errors="coerce")

        # Evaluate strategy — on_candle runs first, then update dashboard metrics
        signal = strategy.on_candle(df, htf_df=htf_df)
        if hasattr(strategy, 'get_dashboard_state'):
            strategy.latest_metrics = strategy.get_dashboard_state(df, htf_df=htf_df)
        if signal:
            signal.instrument_key = instrument_key
            if bar_key:
                signal.metadata["bar_key"] = bar_key
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
        self._paper_positions.clear()
        self._last_evaluated_bar.clear()
        self._managed_positions.clear()
        logger.info("Daily counters reset.")


# Module-level singleton
_engine: AutomationEngine | None = None


def get_engine() -> AutomationEngine:
    """Get or create the AutomationEngine singleton."""
    global _engine
    if _engine is None:
        _engine = AutomationEngine()
    return _engine
