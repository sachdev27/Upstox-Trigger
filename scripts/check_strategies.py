#!/usr/bin/env python3
"""
Check Strategies — validates strategy loading, parameter schemas, and computation.
Run: python -m scripts.check_strategies  (from project root)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from app.strategies.base import StrategyConfig
from app.strategies.supertrend_pro import SuperTrendPro
from app.strategies.indicators import supertrend


def check_strategies():
    results = {"pass": [], "fail": [], "warn": []}

    # 1. Strategy loads
    try:
        config = StrategyConfig(
            name="Test SuperTrend",
            instruments=["NSE_INDEX|Nifty 50"],
            timeframe="15m"
        )
        strategy = SuperTrendPro(config)
        results["pass"].append("✅ SuperTrendPro instantiated successfully")
    except Exception as e:
        results["fail"].append(f"❌ SuperTrendPro failed to load: {e}")
        _print_results(results)
        return False

    # 2. Default params
    defaults = SuperTrendPro.default_params()
    if defaults and len(defaults) > 3:
        results["pass"].append(f"✅ Default params: {len(defaults)} keys")
    else:
        results["warn"].append(f"⚠️ Default params seem sparse: {defaults}")

    # 3. Generate fake OHLCV data
    np.random.seed(42)
    n = 200
    close = 25000 + np.cumsum(np.random.randn(n) * 50)
    high = close + np.abs(np.random.randn(n) * 30)
    low = close - np.abs(np.random.randn(n) * 30)
    open_ = close + np.random.randn(n) * 10
    volume = np.abs(np.random.randn(n) * 100000).astype(int)
    
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="15min").astype(int) // 10**9,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume
    })

    # 4. SuperTrend indicator
    try:
        st_df = supertrend(df, period=10, multiplier=3.0, use_rma=True)
        if "supertrend" in st_df.columns and "trend" in st_df.columns:
            non_nan = st_df["supertrend"].dropna()
            results["pass"].append(f"✅ SuperTrend computed: {len(non_nan)}/{n} non-NaN values")
            
            # Check trend values are 1 or -1
            unique_trends = st_df["trend"].dropna().unique()
            if set(unique_trends).issubset({1, -1, 0}):
                results["pass"].append(f"✅ Trend values valid: {sorted(unique_trends)}")
            else:
                results["warn"].append(f"⚠️ Unexpected trend values: {unique_trends}")
        else:
            results["fail"].append("❌ SuperTrend missing 'supertrend' or 'trend' columns")
    except Exception as e:
        results["fail"].append(f"❌ SuperTrend computation failed: {e}")

    # 5. Strategy on_candle
    try:
        signal = strategy.on_candle(df)
        if signal:
            results["pass"].append(f"✅ on_candle generated signal: {signal.action.value} @ {signal.price:.2f}")
        else:
            results["pass"].append("✅ on_candle returned None (no signal — normal)")
    except Exception as e:
        results["fail"].append(f"❌ on_candle failed: {e}")

    # 6. Dashboard state
    try:
        metrics = strategy.get_dashboard_state(df)
        if metrics and isinstance(metrics, dict):
            results["pass"].append(f"✅ Dashboard state: {len(metrics)} keys")
        else:
            results["warn"].append(f"⚠️ Dashboard state returned: {type(metrics)}")
    except Exception as e:
        results["fail"].append(f"❌ get_dashboard_state failed: {e}")

    _print_results(results)
    return len(results["fail"]) == 0


def _print_results(results):
    print("\n" + "=" * 50)
    print("📊 STRATEGY CHECK RESULTS")
    print("=" * 50)
    for msg in results["pass"]:
        print(msg)
    for msg in results["warn"]:
        print(msg)
    for msg in results["fail"]:
        print(msg)
    print(f"\n📊 {len(results['pass'])} passed, {len(results['warn'])} warnings, {len(results['fail'])} failed")
    print("=" * 50)


if __name__ == "__main__":
    success = check_strategies()
    sys.exit(0 if success else 1)
