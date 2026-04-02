"""
Order Service — execution, management, and risk controls.

Refactored from legacy/order/order.py and legacy/connections.py.
"""

import logging
import time
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
        self.algo_name = (self.settings.ALGO_NAME or "").strip()
        self._daily_pnl: float = 0.0
        self._trade_count: int = 0

    def _get_api_client(self) -> upstox_client.ApiClient:
        """Create an SDK client with required regulatory headers when configured."""
        client = upstox_client.ApiClient(self.config)
        if self.algo_name:
            client.set_default_header("X-Algo-Name", self.algo_name)
        return client

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
            self._get_api_client()
        )
        try:
            response = api.place_order(
                order.to_api_dict(), "3.0", algo_name=self.algo_name or None
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
        entry_order_id = self._extract_order_id(result)
        sl_order_id = None

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
                sl_result = self.place_order(sl_order)
                sl_order_id = self._extract_order_id(sl_result)
            except Exception as e:
                logger.error(f"SL order failed: {e}")

        return {
            "entry": result,
            "entry_order_id": entry_order_id,
            "sl_order_id": sl_order_id,
            "quantity": quantity,
        }

    def _extract_order_id(self, payload: dict | None) -> str | None:
        """Extract broker order_id from varied Upstox response shapes."""
        if not payload:
            return None

        # Common direct keys
        for key in ("order_id", "orderId", "id"):
            val = payload.get(key)
            if val:
                return str(val)

        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("order_id", "orderId", "id"):
                val = data.get(key)
                if val:
                    return str(val)
            # V3 shape: data.order_ids = ["..."]
            order_ids = data.get("order_ids")
            if isinstance(order_ids, list) and order_ids:
                return str(order_ids[0])

        return None

    def extract_order_id(self, payload: dict | None) -> str | None:
        """Public wrapper for order-id extraction used by execution pipeline."""
        return self._extract_order_id(payload)

    def _extract_order_status(self, payload: dict | None) -> str | None:
        """Extract order status from varied Upstox response shapes."""
        if not payload:
            return None

        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("status", "order_status"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val.upper()

        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                for key in ("status", "order_status"):
                    val = first.get(key)
                    if isinstance(val, str) and val:
                        return val.upper()

        # Fall back to envelope status only if nothing found in data.
        # SDK wrapper statuses are typically success/error/partial_success.
        for key in ("status", "order_status"):
            val = payload.get(key)
            if isinstance(val, str) and val:
                up = val.upper()
                if up not in {"SUCCESS", "ERROR", "PARTIAL_SUCCESS"}:
                    return up

        return None

    def wait_for_terminal_order(
        self,
        order_id: str,
        timeout_sec: float = 8.0,
        poll_interval_sec: float = 0.5,
    ) -> dict:
        """
        Poll broker order status until terminal state or timeout.

        Returns:
            {
              "order_id": str,
              "status": str | None,
              "is_filled": bool,
              "is_terminal": bool,
              "timed_out": bool,
            }
        """
        terminal_states = {"COMPLETE", "COMPLETED", "REJECTED", "CANCELLED", "CANCELED"}
        start = time.monotonic()
        last_status = None

        while (time.monotonic() - start) < timeout_sec:
            details = self.get_order_details(order_id)
            status = self._extract_order_status(details)

            # Fallback: check order book if details endpoint shape changes.
            if not status:
                book = self.get_order_book()
                if isinstance(book, list):
                    row = next(
                        (
                            r for r in book
                            if str(r.get("order_id") or r.get("orderId") or "") == str(order_id)
                        ),
                        None,
                    )
                    if row:
                        status = str(row.get("status") or row.get("order_status") or "").upper() or None

            if status:
                last_status = status
                if status in terminal_states:
                    return {
                        "order_id": str(order_id),
                        "status": status,
                        "is_filled": status in {"COMPLETE", "COMPLETED"},
                        "is_terminal": True,
                        "timed_out": False,
                    }

            time.sleep(poll_interval_sec)

        return {
            "order_id": str(order_id),
            "status": last_status,
            "is_filled": False,
            "is_terminal": False,
            "timed_out": True,
        }

    def modify_order(self, order_id: str, modifications: dict) -> dict:
        """Modify an existing order."""
        api = upstox_client.OrderApi(
            self._get_api_client()
        )
        try:
            response = api.modify_order(modifications, "3.0", algo_name=self.algo_name or None)
            return response.to_dict()
        except Exception as e:
            logger.error(f"Order modification failed: {e}")
            raise

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        api = upstox_client.OrderApi(
            self._get_api_client()
        )
        try:
            response = api.cancel_order(order_id, "3.0", algo_name=self.algo_name or None)
            return response.to_dict()
        except Exception as e:
            logger.error(f"Order cancellation failed: {e}")
            raise

    # ── Order Info ───────────────────────────────────────────────

    def get_order_book(self) -> list:
        """Get all orders for today."""
        api = upstox_client.OrderApi(
            self._get_api_client()
        )
        try:
            response = api.get_order_book("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Order book fetch failed: {e}")
            return []

    def get_order_details(self, order_id: str) -> dict | None:
        """Get details for a specific order."""
        api = upstox_client.OrderApi(
            self._get_api_client()
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
            self._get_api_client()
        )
        try:
            response = api.get_trade_history("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Trade history fetch failed: {e}")
            return []

    def get_positions(self) -> list:
        """Get all open positions."""
        api = upstox_client.PortfolioApi(
            self._get_api_client()
        )
        try:
            response = api.get_positions("3.0")
            return response.to_dict().get("data", [])
        except Exception as e:
            logger.error(f"Positions fetch failed: {e}")
            return []

    def get_holdings(self) -> list:
        """Get all equity holdings."""
        api = upstox_client.PortfolioApi(
            self._get_api_client()
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
            self._get_api_client()
        )
        try:
            response = api.get_user_fund_margin("3.0")
            data = response.to_dict().get("data", {})
            # Return equity part by default if both exist
            return data.get("equity", data.get("commodity", {}))
        except Exception as e:
            logger.error(f"Funds fetch failed: {e}")
            return {}

    # ── Risk Management ─────────────────────────────────────────

    def _check_risk_limits(self):
        """Raise if risk limits are breached."""
        max_loss_abs = self.settings.TRADING_CAPITAL * (self.settings.MAX_DAILY_LOSS_PCT / 100)
        if self._daily_pnl < -max_loss_abs:
            raise RuntimeError(
                f"Daily loss limit breached: {self._daily_pnl:.2f} "
                f"(limit: -{max_loss_abs:.2f}, {self.settings.MAX_DAILY_LOSS_PCT}% of capital)"
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

        # Fetch live equity; fall back to configured trading capital
        equity = self.settings.TRADING_CAPITAL
        funds = self.get_funds_and_margin()
        if funds:
            available = funds.get("available_margin") or funds.get("used_margin") or equity
            if available and float(available) > 0:
                equity = float(available)

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
