#!/usr/bin/env python3
"""Quick test of the Instrument Search API lot-size lookup."""

import requests
from app.database.connection import get_session, ConfigSetting

db = get_session()
row = db.query(ConfigSetting).filter_by(key="ACCESS_TOKEN").first()
token = row.value if row else ""
db.close()

print(f"Token length: {len(token)}")

# Test 1: Direct API search by name
print("\n--- Test 1: Search 'NIFTY' CE options ---")
resp = requests.get(
    "https://api.upstox.com/v2/instruments/search",
    params={"query": "NIFTY", "exchanges": "NSE", "segments": "FO", "instrument_types": "CE", "records": 3},
    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    timeout=10,
)
print(f"Status: {resp.status_code}")
items = []
if resp.status_code == 200:
    items = resp.json().get("data", [])
    print(f"Results: {len(items)}")
    for item in items[:3]:
        print(f"  key={item.get('instrument_key')}  lot={item.get('lot_size')}  symbol={item.get('trading_symbol')}")

# Test 2: MarketDataService.get_lot_size with underlying_key
print("\n--- Test 2: get_lot_size with underlying_key ---")
import upstox_client
config = upstox_client.Configuration()
config.access_token = token

from app.market_data.service import MarketDataService
svc = MarketDataService(config)

# Clear cache to force API lookup
MarketDataService._lot_size_cache.clear()

if items:
    test_key = items[0].get("instrument_key")
    print(f"Testing key: {test_key}")

    # Without underlying — should fail gracefully
    lot1 = svc.get_lot_size(test_key)
    print(f"Without underlying: {lot1}")

    # With underlying — should succeed
    MarketDataService._lot_size_cache.clear()
    lot2 = svc.get_lot_size(test_key, underlying_key="NSE_INDEX|Nifty 50")
    print(f"With underlying 'NSE_INDEX|Nifty 50': {lot2}")

    # Verify cache
    lot3 = svc.get_lot_size(test_key)
    print(f"From cache: {lot3}")

    # Test BANKNIFTY
    MarketDataService._lot_size_cache.clear()
    lot4 = svc.get_lot_size("NSE_FO|99999", underlying_key="NSE_INDEX|Nifty Bank")
    print(f"BANKNIFTY lot size: {lot4}")
else:
    print("No test items available")
