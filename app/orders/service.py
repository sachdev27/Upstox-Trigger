"""
Order Service — execution, management, and risk controls.

Refactored from legacy/order/order.py and legacy/connections.py.
"""

import logging
import time
import math
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
        self.api_version = self.settings.ORDER_API_VERSION
        self.algo_name = (self.settings.ALGO_NAME or "").strip()
        self.require_algo_name = bool(self.settings.REQUIRE_ALGO_NAME_FOR_LIVE_ORDERS)
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

        if (not getattr(self.config, "sandbox", False)) and self.require_algo_name and not self.algo_name:
            raise RuntimeError(
                "Live order blocked: ALGO_NAME is required for X-Algo-Name header. "
                "Set ALGO_NAME in settings."
            )

        self._apply_order_defaults(order)

        order.quantity = self._normalize_to_lot_size(order.instrument_token, int(order.quantity or 0))

        api = upstox_client.OrderApi(
            self._get_api_client()
        )
        try:
            response = api.place_order(
                order.to_api_dict(), self.api_version, algo_name=self.algo_name or None
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

    def _apply_order_defaults(self, order: OrderRequest) -> None:
        """Populate request fields with V3-safe defaults when caller leaves them unset."""
        if order.slice is None:
            order.slice = bool(self.settings.AUTO_SLICE_ORDERS)
        if order.market_protection is None:
            order.market_protection = int(self.settings.DEFAULT_MARKET_PROTECTION)

        if order.order_type in {OrderType.MARKET, OrderType.SL_M} and int(order.market_protection) == 0:
            logger.warning(
                "market_protection=0 may be rejected for MARKET/SL-M orders. "
                "Prefer -1 (auto) or 1..25."
            )

    def _normalize_to_lot_size(self, instrument_key: str, requested_qty: int) -> int:
        """
        Ensure quantity is a positive multiple of exchange lot size.

        If lot size is known and requested_qty is not aligned, quantity is rounded up
        to the next valid multiple to avoid broker rejection UDAPI1104.
        """
        qty = max(1, int(requested_qty or 1))

        lot_size = 1
        try:
            from app.database.connection import get_session, Instrument

            session = get_session()
            try:
                inst = session.query(Instrument).filter_by(instrument_key=instrument_key).first()
                if inst and int(inst.lot_size or 1) > 1:
                    lot_size = int(inst.lot_size)
            finally:
                session.close()
        except Exception as e:
            logger.debug(f"Lot size lookup failed for {instrument_key}: {e}")

        # Fallback: use MarketDataService API search if DB doesn't have it
        if lot_size <= 1:
            try:
                from app.market_data.service import MarketDataService
                lot_size = MarketDataService._lot_size_cache.get(instrument_key, 1)
            except Exception:
                pass

        if lot_size <= 1:
            return qty

        if qty % lot_size == 0:
            return qty

        normalized = int(math.ceil(qty / lot_size) * lot_size)
        logger.warning(
            f"Adjusted quantity for {instrument_key}: requested={qty}, "
            f"lot_size={lot_size}, normalized={normalized}"
        )
        return normalized

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
        quantity = int(order.quantity)

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
        # AMO (After Market Order) status — order accepted, will execute at next open
        amo_states = {"AFTER MARKET ORDER REQ RECEIVED", "AMO REQ RECEIVED"}
        filled_states = {"COMPLETE", "COMPLETED"} | amo_states
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
                if status in terminal_states or status in amo_states:
                    return {
                        "order_id": str(order_id),
                        "status": status,
                        "is_filled": status in filled_states,
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
            response = api.modify_order(modifications, self.api_version, algo_name=self.algo_name or None)
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
            response = api.cancel_order(order_id, self.api_version, algo_name=self.algo_name or None)
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
            response = api.get_order_book(self.api_version)
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
                self.api_version, order_id=order_id
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
            response = api.get_trade_history(self.api_version)
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
            response = api.get_positions(self.api_version)
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
            response = api.get_holdings(self.api_version)
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
            response = api.get_user_fund_margin(self.api_version)
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

    # ── GTT (Good Till Triggered) Orders ────────────────────────

    def _get_access_token(self) -> str:
        """Extract access token from the SDK Configuration object."""
        token = getattr(self.config, "access_token", None)
        if not token:
            raise RuntimeError("No access token available for GTT order")
        return token

    def _gtt_headers(self) -> dict:
        """Build HTTP headers for GTT REST API calls."""
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_access_token()}",
        }
        if self.algo_name:
            headers["X-Algo-Name"] = self.algo_name
        return headers

    def place_gtt_order(self, gtt_params: dict) -> dict:
        """
        Place a GTT order via Upstox v3 REST API.

        Args:
            gtt_params: Dict with type, quantity, product, instrument_token,
                        transaction_type, and rules array.

        Returns:
            API response dict with gtt_order_ids on success.
        """
        import json as _json
        import requests as http_requests

        self._check_risk_limits()

        if (not getattr(self.config, "sandbox", False)) and self.require_algo_name and not self.algo_name:
            raise RuntimeError(
                "Live GTT order blocked: ALGO_NAME is required. Set ALGO_NAME in settings."
            )

        url = "https://api.upstox.com/v3/order/gtt/place"
        logger.info(f"GTT request payload:\n{_json.dumps(gtt_params, indent=2)}")
        resp = http_requests.post(url, json=gtt_params, headers=self._gtt_headers(), timeout=15)

        if resp.status_code >= 400:
            logger.error(f"GTT placement failed ({resp.status_code}): {resp.text}")
            logger.error(f"GTT request was: {_json.dumps(gtt_params)}")
            resp.raise_for_status()

        result = resp.json()
        self._trade_count += 1
        logger.info(f"GTT order placed: {gtt_params.get('transaction_type')} "
                     f"{gtt_params.get('quantity')} x {gtt_params.get('instrument_token')} → {result}")
        return result

    def place_gtt_signal(self, signal: TradeSignal, trailing_gap: float = 0.0) -> dict:
        """
        Convert a TradeSignal into a multi-leg GTT order.

        ENTRY  → IMMEDIATE trigger at signal.price (limit order with auto market protection)
        TARGET → IMMEDIATE trigger at signal.take_profit
        STOPLOSS → IMMEDIATE trigger at signal.stop_loss with trailing_gap

        Trailing SL:  The stop-loss automatically follows the market price at a
        fixed distance (trailing_gap).  If trailing_gap is not explicitly provided
        it is derived from the entry→SL distance so the risk:reward ratio is
        preserved while unrealised profit is locked in automatically.

        Upstox constraint: trailing_gap >= 10% of |LTP − SL trigger price|.
        Using the full entry→SL distance satisfies this by definition.
        """
        quantity = max(int(signal.quantity or 1), 1)
        quantity = self._normalize_to_lot_size(signal.instrument_key, quantity)

        # Read GTT execution settings from config (set via frontend Execution tab)
        settings = get_settings()
        gtt_product = getattr(settings, "GTT_PRODUCT_TYPE", "D") or "D"
        gtt_trailing_enabled = getattr(settings, "GTT_TRAILING_SL", True)
        gtt_gap_mode = getattr(settings, "GTT_TRAILING_GAP_MODE", "auto") or "auto"
        gtt_gap_custom = float(getattr(settings, "GTT_TRAILING_GAP_VALUE", 0.0) or 0.0)
        gtt_market_prot = int(getattr(settings, "GTT_MARKET_PROTECTION", -1) or -1)
        gtt_entry_type = getattr(settings, "GTT_ENTRY_TRIGGER_TYPE", "IMMEDIATE") or "IMMEDIATE"

        def _tick_round(price: float, tick: float = 0.05) -> float:
            """Round price to nearest tick size (0.05 for NSE F&O / equity)."""
            return round(round(price / tick) * tick, 2)

        entry_price = _tick_round(float(signal.price))

        rules = [
            {
                "strategy": "ENTRY",
                "trigger_type": gtt_entry_type,
                "trigger_price": entry_price,
                "market_protection": gtt_market_prot,
            }
        ]

        has_tp = signal.take_profit > 0
        has_sl = signal.stop_loss > 0

        if has_tp or has_sl:
            gtt_type = "MULTIPLE"
            if has_tp:
                rules.append({
                    "strategy": "TARGET",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": _tick_round(float(signal.take_profit)),
                })
            if has_sl:
                sl_price = _tick_round(float(signal.stop_loss))

                sl_rule: dict = {
                    "strategy": "STOPLOSS",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": sl_price,
                }

                # Trailing SL: auto-derive gap from entry→SL distance, or use
                # custom value from settings, or explicit caller override.
                if gtt_trailing_enabled:
                    if trailing_gap > 0:
                        effective_gap = trailing_gap
                    elif gtt_gap_mode == "custom" and gtt_gap_custom > 0:
                        effective_gap = gtt_gap_custom
                    else:
                        effective_gap = abs(entry_price - sl_price)

                    effective_gap = _tick_round(effective_gap)
                    if effective_gap > 0:
                        sl_rule["trailing_gap"] = effective_gap

                rules.append(sl_rule)
        else:
            gtt_type = "SINGLE"

        gtt_params = {
            "type": gtt_type,
            "quantity": quantity,
            "product": gtt_product,
            "instrument_token": signal.instrument_key,
            "transaction_type": signal.action.value,
            "rules": rules,
        }

        result = self.place_gtt_order(gtt_params)

        gtt_ids = []
        if isinstance(result, dict):
            data = result.get("data", {})
            if isinstance(data, dict):
                gtt_ids = data.get("gtt_order_ids", [])

        return {
            "gtt_response": result,
            "gtt_order_ids": gtt_ids,
            "gtt_order_id": gtt_ids[0] if gtt_ids else None,
            "quantity": quantity,
        }

    def cancel_gtt_order(self, gtt_order_id: str) -> dict:
        """Cancel a pending GTT order."""
        import requests as http_requests

        url = "https://api.upstox.com/v3/order/gtt/cancel"
        payload = {"gtt_order_id": gtt_order_id}
        resp = http_requests.delete(url, json=payload, headers=self._gtt_headers(), timeout=15)

        if resp.status_code >= 400:
            logger.error(f"GTT cancel failed ({resp.status_code}): {resp.text}")
            resp.raise_for_status()

        result = resp.json()
        logger.info(f"GTT order cancelled: {gtt_order_id} → {result}")
        return result
