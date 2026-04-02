#!/usr/bin/env python3
"""Troubleshoot Upstox static-IP setup without placing live orders.

This script helps separate three common failure modes:
1. Proxy/egress IP is wrong.
2. Access token is invalid or revoked.
3. Unrestricted APIs work, so remaining failures are likely static-IP or algo-order compliance issues.

Static-IP restrictions apply to order-mutating APIs such as place/modify/cancel.
This script intentionally avoids those endpoints.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.auth.service import AuthService
from app.config import get_settings
from app.market_data.service import MarketDataService
from app.network_proxy import configure_network_proxies
from app.orders.service import OrderService


def mask(value: str, keep: int = 6) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return value
    return f"{value[:keep]}...{value[-4:]}"


def print_section(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print(f"{'=' * 72}")


def print_line(label: str, value: str) -> None:
    print(f"{label:<32} {value}")


def check_proxy_egress(settings) -> tuple[bool, str]:
    proxies = {
        "http": (settings.REQUESTS_HTTP_PROXY or "").strip() or (settings.UPSTOX_PROXY_URL or "").strip(),
        "https": (settings.REQUESTS_HTTPS_PROXY or "").strip() or (settings.UPSTOX_PROXY_URL or "").strip(),
    }

    if not proxies["http"] and not proxies["https"]:
        return False, "No proxy configured"

    try:
        response = requests.get("https://api.ipify.org", proxies=proxies, timeout=20)
        response.raise_for_status()
        return True, response.text.strip()
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    settings = get_settings()
    settings.load_from_db()
    configure_network_proxies(settings)
    auth = AuthService()

    print_section("Upstox Static-IP Troubleshooter")
    print_line("Mode", "SANDBOX" if settings.USE_SANDBOX else "LIVE")
    print_line("API key", mask(settings.API_KEY))
    print_line("Algo name", settings.ALGO_NAME or "<missing>")
    print_line("Algo id", settings.ALGO_ID or "<missing>")
    print_line("Proxy required", str(bool(settings.REQUIRE_UPSTOX_PROXY)))
    print_line("Upstox proxy", "configured" if settings.UPSTOX_PROXY_URL else "missing")
    print_line("HTTP proxy", "configured" if settings.REQUESTS_HTTP_PROXY else "missing")
    print_line("HTTPS proxy", "configured" if settings.REQUESTS_HTTPS_PROXY else "missing")

    print_section("Proxy Egress")
    proxy_ok, proxy_result = check_proxy_egress(settings)
    if proxy_ok:
        print_line("Public IP via proxy", proxy_result)
    else:
        print_line("Proxy check", f"FAILED: {proxy_result}")

    print_section("Token Validation")
    token_ok, token_reason = auth.validate_token(use_sandbox=settings.USE_SANDBOX)
    print_line("Token valid", str(token_ok))
    if token_reason:
        print_line("Reason", token_reason)
    if not token_ok:
        print_line("Re-auth URL", auth.get_auth_url())

    print_section("Unrestricted API Checks")
    if token_ok:
        try:
            config = auth.get_configuration(use_sandbox=settings.USE_SANDBOX)
            market = MarketDataService(config)
            orders = OrderService(config)

            profile = market.get_profile()
            print_line("Profile API", "OK" if profile else "EMPTY/FAILED")

            funds = orders.get_funds_and_margin()
            print_line("Funds API", "OK" if funds else "EMPTY/FAILED")

            positions = orders.get_positions()
            print_line("Positions API", f"OK ({len(positions)} records)")

            option_contracts = market.get_option_contracts(settings.NIFTY)
            print_line("Option contracts API", f"OK ({len(option_contracts)} records)")
        except Exception as exc:
            print_line("Unrestricted API", f"FAILED: {exc}")
    else:
        print_line("Skipped", "Token invalid, unrestricted API results would be misleading")

    print_section("Interpretation")
    if not token_ok:
        print("- Current blocker is authentication, not static IP. Fix token first.")
        print("- Upstox can revoke tokens after app/IP/algo configuration changes.")
    elif not proxy_ok:
        print("- Token is valid, but proxy egress could not be confirmed.")
        print("- Fix proxy before testing order placement APIs.")
    else:
        print("- Proxy egress and token look healthy.")
        print("- If order placement still fails while unrestricted APIs work, the issue is likely static-IP or algo-order compliance.")
        print("- Static-IP restrictions apply to order-mutating APIs, not holdings/positions/funds/historical data.")

    return 0 if (proxy_ok and token_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())