#!/usr/bin/env python3
import sys
import os
import json
from datetime import datetime, timedelta

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from app.market_data.service import MarketDataService
from app.auth.service import get_auth_service

def check_limit(svc, instrument, interval, days):
    try:
        from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        to_date = datetime.now().strftime('%Y-%m-%d')
        candles = svc.get_historical_candles(instrument, interval, from_date, to_date)
        return len(candles) if candles else 0
    except Exception:
        return -1

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_data_limits.py <instrument_key> [interval]")
        print("Example: python3 scripts/check_data_limits.py 'NSE_INDEX|Nifty 50' 15minute")
        sys.exit(1)

    instrument = sys.argv[1]
    interval = sys.argv[2] if len(sys.argv) > 2 else "15minute"
    
    auth = get_auth_service()
    config = auth.get_configuration(use_sandbox=False)
    svc = MarketDataService(config)

    print(f"🔍 Checking data availability for {instrument} at {interval} interval...")
    print("-" * 60)
    print(f"{'Days Back':<15} | {'Candles Found':<15} | {'Status'}")
    print("-" * 60)

    # Expanded test ranges to show large limits
    test_days = [3650, 1825, 365, 180, 90, 60, 30, 25, 20]
    
    for d in test_days:
        # Skip absurd ranges for 1m
        if interval in ["1m", "1minute", "5m", "15m"] and d > 31:
            continue
            
        count = check_limit(svc, instrument, interval, d)
        if count > 0:
            status = "✅ OK"
        elif count == 0:
            status = "❌ Empty / Rejected"
        else:
            status = "💥 Error (Limit?)"
        print(f"{d:<15} | {count:<15} | {status}")

    print("-" * 60)
    print("Done.")
