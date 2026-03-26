"""
Order Service — execution, management, and risk controls.

Refactored from legacy/order/order.py and legacy/connections.py.
"""

import logging
from datetime import datetime, timezone, timedelta

import upstox_client

from app.config import get_settings
from app.database.connection import get_session, Instrument
from app.orders.models import OrderRequest, TradeSignal, OrderType, TransactionType

logger = logging.getLogger(__name__)


class OrderService:
    """Handles order placement, modification, cancellation, and risk controls."""

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self.settings = get_settings()
        self.api_version = self.settings.API_VERSION
        self._daily_pnl: float = 0.0
        self._trade_count: int = 0

    # ── Order Execution ─────────────────────────────────────────

    def place_order(self, order: OrderRequest) -> dict:
        """
        Place an order after risk checks.

        Returns:
            API response dict with order_id on success.
        """
        # Risk checks
        self._check_risk_limits()

        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.place_order(
                order.to_api_dict(), "3.0"
            )
            self._trade_count += 1
            result = response.to_dict()
            order_id = result.get("data", {}).get("order_id")
            
            logger.info(
                f"Order placed: {order.transaction_type.value} "
                f"{order.quantity} x {order.instrument_token} "
                f"({order.order_type.value}) → {result}"
            )

            # Log to Database for History & Visibility
            try:
                from app.database.connection import get_session, TradeLog
                session = get_session()
                # Determine strategy name from tag (auto-StrategyName)
                tag = order.tag or ""
                strategy_name = tag.split("-")[-1] if "-" in tag else "MANUAL"
                
                log = TradeLog(
                    timestamp=datetime.now(),
                    strategy_name=strategy_name,
                    instrument_key=order.instrument_token,
                    action=order.transaction_type.value,
                    quantity=order.quantity,
                    price=order.price or 0.0,
                    order_id=order_id,
                    status="filled" if order.order_type == OrderType.MARKET else "pending",
                    metadata_json={"tag": order.tag, "product": order.product.value}
                )
                session.add(log)
                session.commit()
                session.close()
            except Exception as db_err:
                logger.error(f"Failed to log order to DB: {db_err}")

            return result
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def place_signal(self, signal: TradeSignal) -> dict:
        """
        Convert a strategy signal into an order and place it.
        Uses risk-based position sizing if quantity is 0.
        """
        quantity = signal.quantity
        if quantity == 0:
            quantity = self._calculate_position_size(
                signal.instrument_key, signal.price, signal.stop_loss
            )

        logger.info(
            f"📋 Signal → Order: {signal.action.value} {signal.instrument_key} | "
            f"Qty={quantity} | Entry~{signal.price:.2f} | "
            f"SL={signal.stop_loss:.2f} | TP={signal.take_profit:.2f} | "
            f"Strategy={signal.strategy_name}"
        )

        order = OrderRequest(
            instrument_token=signal.instrument_key,
            quantity=quantity,
            transaction_type=signal.action,
            order_type=OrderType.MARKET,
            slice=True,  # V3 Auto-Slicing enabled by default for automation
            tag=f"auto-{signal.strategy_name}",
        )

        result = self.place_order(order)

        # Place stop-loss if provided
        if signal.stop_loss > 0:
            sl_side = (
                TransactionType.SELL
                if signal.action == TransactionType.BUY
                else TransactionType.BUY
            )
            sl_order = OrderRequest(
                instrument_token=signal.instrument_key,
                quantity=quantity,
                transaction_type=sl_side,
                order_type=OrderType.SL_M,
                trigger_price=signal.stop_loss,
                tag=f"sl-{signal.strategy_name}",
            )
            try:
                self.place_order(sl_order)
            except Exception as e:
                logger.error(f"SL order failed: {e}")

        # Place take-profit if provided
        if signal.take_profit > 0:
            tp_side = (
                TransactionType.SELL
                if signal.action == TransactionType.BUY
                else TransactionType.BUY
            )
            tp_order = OrderRequest(
                instrument_token=signal.instrument_key,
                quantity=quantity,
                transaction_type=tp_side,
                order_type=OrderType.LIMIT,
                price=signal.take_profit,
                tag=f"tp-{signal.strategy_name}",
            )
            try:
                self.place_order(tp_order)
            except Exception as e:
                logger.error(f"TP order failed: {e}")

        return result

    def modify_order(self, order_id: str, modifications: dict) -> dict:
        """Modify an existing order."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.modify_order(modifications, "3.0")
            return response.to_dict()
        except Exception as e:
            logger.error(f"Order modification failed: {e}")
            raise

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.cancel_order(order_id, "3.0")
            return response.to_dict()
        except Exception as e:
            logger.error(f"Order cancellation failed: {e}")
            raise

    # ── Order Info ───────────────────────────────────────────────

    def get_order_book(self) -> list:
        """Get all orders for today."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_order_book("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            # Sandbox often doesn't support Order Info APIs (404)
            if self.config.host == "https://api-sandbox.upstox.com" and "404" in str(e):
                logger.debug("Sandbox: Order Book API 404 (returning empty list)")
                return []
            logger.error(f"Order book fetch failed: {e}")
            return []

    def get_order_details(self, order_id: str) -> dict | None:
        """Get details for a specific order."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_order_details(
                "3.0", order_id=order_id
            )
            return response.to_dict()
        except Exception as e:
            logger.error(f"Order details fetch failed: {e}")
            return None

    def get_trade_history(self) -> list:
        """Get trade history for today."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_trade_history("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            # Sandbox often doesn't support Trade History APIs (404)
            if self.config.host == "https://api-sandbox.upstox.com" and "404" in str(e):
                logger.debug("Sandbox: Trade History API 404 (returning empty list)")
                return []
            logger.error(f"Trade history fetch failed: {e}")
            return []

    def get_positions(self) -> list:
        """Get all open positions."""
        api = upstox_client.PortfolioApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_positions("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            # Sandbox often doesn't support Portfolio APIs (404)
            if self.config.host == "https://api-sandbox.upstox.com" and "404" in str(e):
                logger.debug("Sandbox: Positions API 404 (returning empty list)")
                return []
            logger.error(f"Positions fetch failed: {e}")
            return []

    def get_holdings(self) -> list:
        """Get all equity holdings."""
        api = upstox_client.PortfolioApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_holdings("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Holdings fetch failed: {e}")
            return []

    def get_funds_and_margin(self) -> dict:
        """Get account funds and margin details."""
        api = upstox_client.UserApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_user_fund_margin("3.0")
            data = response.to_dict().get("data", {})
            # Return equity part by default if both exist
            return data.get("equity", data.get("commodity", {}))
        except Exception as e:
            # Sandbox often doesn't support Funds API (404)
            if self.config.host == "https://api-sandbox.upstox.com" and "404" in str(e):
                logger.debug("Sandbox: Funds API 404 (returning mock margin)")
                return {"available_margin": 1000000.0, "used_margin": 0.0}
            logger.error(f"Funds fetch failed: {e}")
            return {}

    # ── Risk Management ─────────────────────────────────────────

    def _check_risk_limits(self):
        """Raise if risk limits are breached."""
        if self._daily_pnl < -(self.settings.MAX_DAILY_LOSS_PCT):
            raise RuntimeError(
                f"Daily loss limit breached: {self._daily_pnl:.2f}% "
                f"(limit: {self.settings.MAX_DAILY_LOSS_PCT}%)"
            )

    def _calculate_position_size(
        self, instrument_token: str, entry_price: float, stop_loss: float
    ) -> int:
        """
        Refined Risk-based position sizing + Lot Size enforcement.
        
        Logic:
        1. Calculate 'Point Risk' (distance from entry to SL).
        2. Fetch 'Lot Size' for the instrument.
        3. Risk per Lot = Point Risk * Lot Size.
        4. Number of Lots = Total Risk Amount / Risk per Lot.
        """
        if stop_loss <= 0 or entry_price <= 0:
            return 1

        point_risk = abs(entry_price - stop_loss)
        if point_risk == 0:
            return 1

        # 1. Determine Total Risk Amount (default 1% of equity)
        equity = 100_000
        try:
            funds = self.get_funds_and_margin()
            equity = float(funds.get("available_margin", 100_000))
        except Exception as e:
            logger.warning(f"Funds fetch failed, using default equity {equity}: {e}")
            
        total_risk_allowance = equity * (self.settings.MAX_RISK_PER_TRADE_PCT / 100)
        
        # 2. Fetch lot metadata from Database
        lot_size = 1
        min_lot = 1
        try:
            session = get_session()
            instr = session.query(Instrument).filter_by(instrument_key=instrument_token).first()
            if instr:
                lot_size = instr.lot_size
                min_lot = instr.minimum_lot or lot_size
            session.close()
        except Exception as e:
            logger.warning(f"Instrument DB lookup failed for {instrument_token}, using defaults: {e}")

        # 3. Calculate Lots
        # Risk per lot = points_lost_on_SL * units_per_lot
        risk_per_lot = point_risk * lot_size
        
        # How many lots can we afford to lose?
        num_lots = int(total_risk_allowance // risk_per_lot)
        
        # 4. Final Quantity (always >= 1 minimum lot)
        final_qty = max(min_lot, num_lots * lot_size)
        
        logger.info(
            f"Sizing: Risk={total_risk_allowance:.0f} | "
            f"PointRisk={point_risk:.2f} | LotSize={lot_size} | MinLot={min_lot} | "
            f"Lots={num_lots} → Qty={final_qty}"
        )
        
        return final_qty

    def is_market_hours(self) -> bool:
        """Check if Indian market is currently open (9:15 AM – 3:30 PM IST)."""
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close and now.weekday() < 5
