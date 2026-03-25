"""
Order Service — execution, management, and risk controls.

Refactored from legacy/order/order.py and legacy/connections.py.
"""

import logging
from datetime import datetime, timezone, timedelta

import upstox_client

from app.config import get_settings
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
                order.to_api_dict(), self.api_version
            )
            self._trade_count += 1
            result = response.to_dict()
            logger.info(
                f"Order placed: {order.transaction_type.value} "
                f"{order.quantity} x {order.instrument_token} "
                f"({order.order_type.value}) → {result}"
            )
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
                signal.price, signal.stop_loss
            )

        order = OrderRequest(
            instrument_token=signal.instrument_key,
            quantity=quantity,
            transaction_type=signal.action,
            order_type=OrderType.MARKET,
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

        return result

    def modify_order(self, order_id: str, modifications: dict) -> dict:
        """Modify an existing order."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.modify_order(modifications, self.api_version)
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
            response = api.cancel_order(order_id, self.api_version)
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
            response = api.get_order_book(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Order book fetch failed: {e}")
            return []

    def get_order_details(self, order_id: str) -> dict | None:
        """Get details for a specific order."""
        api = upstox_client.OrderApi(
            upstox_client.ApiClient(self.config)
        )
        try:
            response = api.get_order_details(
                self.api_version, order_id=order_id
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
            response = api.get_trade_history(self.api_version)
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Trade history fetch failed: {e}")
            return []

    # ── Risk Management ─────────────────────────────────────────

    def _check_risk_limits(self):
        """Raise if risk limits are breached."""
        if self._daily_pnl < -(self.settings.MAX_DAILY_LOSS_PCT):
            raise RuntimeError(
                f"Daily loss limit breached: {self._daily_pnl:.2f}% "
                f"(limit: {self.settings.MAX_DAILY_LOSS_PCT}%)"
            )

    def _calculate_position_size(
        self, entry_price: float, stop_loss: float
    ) -> int:
        """
        Risk-based position sizing.
        Ensures that a SL hit costs exactly MAX_RISK_PER_TRADE_PCT of equity.
        """
        if stop_loss <= 0 or entry_price <= 0:
            return 1

        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            return 1

        # rough equity estimate — in production, fetch from API
        equity = 100_000  # TODO: fetch from get_funds_and_margin
        risk_amount = equity * (self.settings.MAX_RISK_PER_TRADE_PCT / 100)
        qty = int(risk_amount / risk_per_unit)
        return max(1, qty)

    def is_market_hours(self) -> bool:
        """Check if Indian market is currently open (9:15 AM – 3:30 PM IST)."""
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close and now.weekday() < 5
