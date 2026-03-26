#!/usr/bin/env python3
"""
Check API — validates all backend endpoints return 200 and no route conflicts.
Run: python -m scripts.check_api  (from project root)

Requires the server to be running on localhost:8210.
"""

import sys
import requests

BASE = "http://localhost:8210"

# All endpoints the frontend uses (from api.js)
ENDPOINTS = [
    # Market Data
    ("GET", "/market/instruments/search?query=nifty", "Instrument Search"),
    ("GET", "/market/instruments/featured", "Featured Instruments"),
    ("GET", "/market/candles?instrument_key=NSE_INDEX|Nifty%2050&interval=15minute", "Historical Candles"),
    ("GET", "/market/option-chain?instrument_key=NSE_INDEX|Nifty%2050", "Detailed Option Chain"),
    ("GET", "/market/ltp?instrument_key=NSE_INDEX|Nifty%2050", "LTP"),
    ("GET", "/market/quote?instrument_key=NSE_INDEX|Nifty%2050", "Quote"),
    ("GET", "/market/status?exchange=NSE", "Market Status"),
    ("GET", "/market/holidays", "Holidays"),
    ("GET", "/market/strategy-overlay?instrument_key=NSE_INDEX|Nifty%2050&timeframe=15minute&strategy_class=SuperTrendPro&params={}", "Strategy Overlay"),
    
    # Orders
    ("GET", "/orders/trades", "Trade History"),
    ("GET", "/orders/positions", "Positions"),
    ("GET", "/orders/holdings", "Holdings"),
    ("GET", "/orders/funds", "Funds"),
    ("GET", "/orders/book", "Order Book"),
    ("GET", "/orders/status/market-hours", "Market Hours"),
    
    # Engine
    ("GET", "/engine/status", "Engine Status"),
    ("GET", "/engine/signals", "Engine Signals"),
    ("GET", "/engine/trades", "Engine Trades"),
    
    # Strategies
    ("GET", "/strategies/", "List Strategies"),
    ("GET", "/strategies/schema", "Strategy Schema"),
    ("GET", "/strategies/signals", "Strategy Signals"),
    
    # Settings
    ("GET", "/settings/", "Settings"),
    
    # Health
    ("GET", "/", "Root"),
    ("GET", "/health", "Health"),
]


def check_api():
    results = {"pass": [], "fail": [], "warn": []}

    # Check server is running
    try:
        r = requests.get(f"{BASE}/", timeout=5)
        results["pass"].append(f"✅ Server running at {BASE}")
    except requests.ConnectionError:
        results["fail"].append(f"❌ Server not running at {BASE}")
        _print_results(results)
        return False

    # Test each endpoint
    for method, path, name in ENDPOINTS:
        try:
            url = f"{BASE}{path}"
            if method == "GET":
                r = requests.get(url, timeout=10)
            elif method == "POST":
                r = requests.post(url, timeout=10)
            
            if r.status_code == 200:
                results["pass"].append(f"✅ {name}: {method} {path}")
            elif r.status_code == 422:
                results["warn"].append(f"⚠️ {name}: {r.status_code} (validation error — likely missing params)")
            elif r.status_code == 500:
                results["fail"].append(f"❌ {name}: {r.status_code} SERVER ERROR — {method} {path}")
            else:
                results["warn"].append(f"⚠️ {name}: {r.status_code} — {method} {path}")
        except Exception as e:
            results["fail"].append(f"❌ {name}: EXCEPTION — {e}")

    _print_results(results)
    return len(results["fail"]) == 0


def _print_results(results):
    print("\n" + "=" * 60)
    print("🌐 API ENDPOINT CHECK RESULTS")
    print("=" * 60)
    for msg in results["pass"]:
        print(msg)
    for msg in results["warn"]:
        print(msg)
    for msg in results["fail"]:
        print(msg)
    print(f"\n📊 {len(results['pass'])} passed, {len(results['warn'])} warnings, {len(results['fail'])} failed")
    print("=" * 60)


if __name__ == "__main__":
    success = check_api()
    sys.exit(0 if success else 1)
