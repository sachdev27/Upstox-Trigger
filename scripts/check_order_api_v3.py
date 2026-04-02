"""Safe validation for Upstox OrderApiV3 request construction.

This script intentionally does NOT place a live order. It verifies that:
1. The SDK exposes OrderApiV3 and PlaceOrderV3Request.
2. Repo auth wiring can build an authenticated SDK configuration.
3. A PlaceOrderV3Request can be constructed from sample values.
4. The API client instance can be created successfully.

Usage:
    ./venv/bin/python scripts/check_order_api_v3.py
    ./.venv/bin/python scripts/check_order_api_v3.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import upstox_client


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.service import get_auth_service  # noqa: E402
from app.database.connection import get_session, ConfigSetting  # noqa: E402


def _load_token_from_db(use_sandbox: bool = False) -> str:
    key = "SANDBOX_ACCESS_TOKEN" if use_sandbox else "ACCESS_TOKEN"
    session = get_session()
    try:
        row = session.query(ConfigSetting).filter_by(key=key).first()
        return str(row.value).strip() if row and row.value else ""
    finally:
        session.close()


def main() -> int:
    print("Checking Upstox OrderApiV3 support...")

    has_order_api_v3 = hasattr(upstox_client, "OrderApiV3")
    has_request_cls = hasattr(upstox_client, "PlaceOrderV3Request")

    print(f"OrderApiV3 available: {has_order_api_v3}")
    print(f"PlaceOrderV3Request available: {has_request_cls}")

    if not (has_order_api_v3 and has_request_cls):
        print("SDK does not expose the required V3 order classes.")
        return 1

    auth = get_auth_service()
    auth.settings.load_from_db()

    use_sandbox = bool(auth.settings.USE_SANDBOX)
    db_token = _load_token_from_db(use_sandbox=use_sandbox)
    if db_token:
        if use_sandbox:
            auth.settings.SANDBOX_ACCESS_TOKEN = db_token
        else:
            auth.settings.ACCESS_TOKEN = db_token

    config = auth.get_configuration(use_sandbox=False)
    token_present = bool(getattr(config, "access_token", None))
    print(f"Access token configured: {token_present}")
    print(f"Configured proxy: {getattr(config, 'proxy', None) or 'None'}")

    try:
        api_client = upstox_client.ApiClient(config)
    except Exception as exc:
        print(f"Initial ApiClient creation failed: {exc}")
        proxy = getattr(config, "proxy", None) or ""
        if proxy and not str(proxy).startswith(("http://", "https://")):
            print("Retrying dry-run with proxy disabled because SDK client rejected proxy scheme...")
            config_no_proxy = copy.copy(config)
            config_no_proxy.proxy = None
            api_client = upstox_client.ApiClient(config_no_proxy)
            print("ApiClient created successfully with proxy disabled for dry-run.")
        else:
            raise

    api_instance = upstox_client.OrderApiV3(api_client)

    body = upstox_client.PlaceOrderV3Request(
        quantity=1,
        product="D",
        validity="DAY",
        price=0,
        tag="validation-only",
        instrument_token="NSE_EQ|INE528G01035",
        order_type="MARKET",
        transaction_type="BUY",
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False,
        slice=True,
    )

    body_dict = body.to_dict() if hasattr(body, "to_dict") else body.__dict__

    print("\nConstructed request payload:")
    print(json.dumps(body_dict, indent=2, default=str))

    place_order_method = getattr(api_instance, "place_order", None)
    print(f"\nplace_order callable: {callable(place_order_method)}")
    print("No live order was sent. This is a dry-run wiring check only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())