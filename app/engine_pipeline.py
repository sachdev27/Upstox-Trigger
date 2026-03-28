import logging
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
from app.config import get_settings
from app.notifications.manager import get_notification_manager
from app.database.connection import get_session, TradeLog

if TYPE_CHECKING:
    from app.engine import AutomationEngine
    from app.orders.models import TradeSignal
    from app.strategies.base import StrategyConfig

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

class SignalProcessor(ABC):
    """Abstract base class for signal processing steps."""
    @abstractmethod
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        """Process a signal. Returns False to halt the pipeline."""
        pass

class RiskGuardProcessor(SignalProcessor):
    """Checks for trading side restrictions and daily loss limits."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        # Trading Side check
        if engine.trading_side == "LONG_ONLY" and signal.action.value == "SELL":
            logger.info("🚫 SHORT signal skipped (LONG_ONLY mode)")
            return False
        if engine.trading_side == "SHORT_ONLY" and signal.action.value == "BUY":
            logger.info("🚫 LONG signal skipped (SHORT_ONLY mode)")
            return False

        # Daily Loss check
        max_loss_abs = engine.trading_capital * (engine.max_daily_loss_pct / 100)
        if engine._daily_pnl <= -max_loss_abs:
            logger.warning(
                f"🛑 MAX DAILY LOSS HIT ({-engine._daily_pnl:.2f} >= {max_loss_abs:.2f}). "
                f"Blocking {signal.action.value} on {signal.instrument_key}."
            )
            engine.auto_mode = False
            return False
        return True

class ATMResolverProcessor(SignalProcessor):
    """Resolves index instruments to their closest ATM option contract."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        if ("INDEX" in signal.instrument_key or signal.instrument_key in ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]) and engine._market_service:
            try:
                logger.info(f"🔍 Resolving ATM option for {signal.instrument_key} @ {signal.price}")
                # Use the service directly
                chain_data = await engine._market_service.get_detailed_option_chain(signal.instrument_key)
                if chain_data["status"] == "success" and chain_data["chain"]:
                    matrix = chain_data["chain"]
                    closest = min(matrix, key=lambda x: abs(x["strike_price"] - signal.price))
                    
                    # Logic for CE/PE
                    side = "ce" if signal.action.value == "BUY" else "pe"
                    opt = closest.get(side)
                    if opt:
                        # Store original key and update current for execution
                        signal.metadata["underlying"] = signal.instrument_key
                        signal.instrument_key = opt["instrument_key"]
                        logger.info(f"🎯 Resolved ATM {side.upper()}: {signal.instrument_key} (Strike: {closest['strike_price']})")
                    else:
                        logger.warning(f"No {side.upper()} available for ATM strike {closest['strike_price']}")
            except Exception as e:
                logger.error(f"Option resolution failed: {e}")
        return True

class ExecutionProcessor(SignalProcessor):
    """Handles paper or live order execution and database logging."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        is_paper = engine.paper_trading or config.paper_trading
        trade_instrument = signal.instrument_key
        
        if is_paper:
            logger.info(f"📝 [PAPER] {signal.action.value} {trade_instrument} @ {signal.price:.2f}")
            engine._trades_today.append({
                "timestamp": datetime.now(IST).isoformat(),
                "type": "paper",
                "strategy": signal.strategy_name or config.name,
                "instrument": trade_instrument,
                "underlying": signal.metadata.get("underlying", signal.instrument_key),
                "action": signal.action.value,
                "price": signal.price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "score": signal.confidence_score,
            })

            # DB Log
            try:
                session = get_session()
                log = TradeLog(
                    timestamp=datetime.now(IST),
                    strategy_name=signal.strategy_name or config.name,
                    instrument_key=trade_instrument,
                    action=signal.action.value,
                    quantity=signal.quantity,
                    price=signal.price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    status="paper",
                    metadata_json={"underlying": signal.metadata.get("underlying", signal.instrument_key), **(signal.metadata or {})}
                )
                session.add(log)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"DB log failed: {e}")
        else:
            # Live execution logic
            try:
                result = engine._order_service.place_signal(signal)
                logger.info(f"💰 [LIVE] Order placed: {result}")
                engine._trades_today.append({
                    "timestamp": datetime.now(IST).isoformat(),
                    "type": "live",
                    "strategy": signal.strategy_name or config.name,
                    "instrument": trade_instrument,
                    "action": signal.action.value,
                    "price": signal.price,
                    "order_result": result,
                })
            except Exception as e:
                logger.error(f"❌ Order execution failed: {e}")
                return False
        return True

class AlerterProcessor(SignalProcessor):
    """Sends notifications (Email) for trade signals and persists to ActiveSignal."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        # 1. Persist to ActiveSignal table
        try:
            from app.database.connection import ActiveSignal
            session = get_session()
            active_sig = ActiveSignal(
                strategy_name=signal.strategy_name or config.name,
                instrument_key=signal.instrument_key,
                timeframe=config.timeframe,
                action=signal.action.value,
                price=signal.price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence_score=signal.confidence_score,
                status="active",
                metadata_json=signal.metadata or {},
            )
            session.add(active_sig)
            session.commit()
            session.close()
        except Exception as e:
            logger.error(f"Failed to persist ActiveSignal: {e}")

        # 2. Resolve instrument name from watchlist
        instrument_name = signal.instrument_key
        instrument_symbol = signal.instrument_key.split("|")[-1] if "|" in signal.instrument_key else signal.instrument_key
        try:
            from app.database.connection import Watchlist
            wl_session = get_session()
            wl_item = wl_session.query(Watchlist).filter_by(instrument_key=signal.instrument_key).first()
            if wl_item:
                instrument_name = wl_item.name or wl_item.symbol or instrument_name
                instrument_symbol = wl_item.symbol or instrument_symbol
            wl_session.close()
        except Exception:
            pass

        # 3. Send notification
        manager = get_notification_manager()
        is_paper = engine.paper_trading or config.paper_trading
        mode_str = "📋 PAPER" if is_paper else "🔴 LIVE"
        action_emoji = "🟢" if signal.action.value == "BUY" else "🔴"
        
        subject = f"🎯 {signal.action.value} Signal: {instrument_symbol} ({instrument_name})"
        
        body = (
            f"{'━' * 40}\n"
            f"  {action_emoji} {signal.action.value} SIGNAL — {mode_str}\n"
            f"{'━' * 40}\n\n"
            f"📌 Instrument:\n"
            f"   Symbol:    {instrument_symbol}\n"
            f"   Name:      {instrument_name}\n"
            f"   Key:       {signal.instrument_key}\n\n"
            f"📊 Strategy:  {signal.strategy_name or config.name}\n"
            f"⏱️ Timeframe: {config.timeframe}\n\n"
            f"{'─' * 40}\n"
            f"  💰 PRICE LEVELS\n"
            f"{'─' * 40}\n"
            f"   Entry:       ₹{signal.price:.2f}\n"
            f"   Stop Loss:   ₹{signal.stop_loss:.2f}\n"
            f"   Take Profit: ₹{signal.take_profit:.2f}\n\n"
            f"   Confidence:  {signal.confidence_score}/100\n"
            f"   Time:        {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S IST')}\n"
            f"{'━' * 40}\n"
        )
        
        asyncio.create_task(manager.send_alert(subject, body))
        return True

class BroadcastProcessor(SignalProcessor):
    """Broadcasts signal/trade updates to connected UI clients via WebSocket."""
    async def process(self, signal: 'TradeSignal', config: 'StrategyConfig', engine: 'AutomationEngine') -> bool:
        if engine.broadcast_callback:
            is_paper = engine.paper_trading or config.paper_trading
            asyncio.create_task(engine.broadcast_callback({
                "type": "trade_executed",
                "data": {
                    "type": "paper" if is_paper else "live",
                    "strategy": signal.strategy_name or config.name,
                    "instrument": signal.instrument_key,
                    "action": signal.action.value,
                    "price": signal.price
                }
            }))
        return True
