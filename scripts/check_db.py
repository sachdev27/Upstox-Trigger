#!/usr/bin/env python3
"""
Check DB — validates tables exist, config_settings populated, instruments seeded.
Run: python -m scripts.check_db  (from project root)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import init_db, get_session, get_engine
from sqlalchemy import inspect


def check_db():
    results = {"pass": [], "fail": [], "warn": []}

    # 1. Engine connects
    try:
        engine = get_engine()
        results["pass"].append(f"✅ Database engine created: {engine.url}")
    except Exception as e:
        results["fail"].append(f"❌ Database engine failed: {e}")
        _print_results(results)
        return False

    # 2. Tables exist
    inspector = inspect(engine)
    expected_tables = ["trade_logs", "strategy_states", "candle_cache", "config_settings", "instruments", "market_ticks"]
    existing = inspector.get_table_names()
    
    for table in expected_tables:
        if table in existing:
            results["pass"].append(f"✅ Table '{table}' exists")
        else:
            results["fail"].append(f"❌ Table '{table}' MISSING — run init_db()")

    # 3. Config settings populated
    session = get_session()
    try:
        from app.database.connection import ConfigSetting
        count = session.query(ConfigSetting).count()
        if count > 0:
            results["pass"].append(f"✅ config_settings: {count} entries")
            
            # Check critical keys
            critical_keys = ["API_KEY", "API_SECRET", "ACCESS_TOKEN"]
            for key in critical_keys:
                setting = session.query(ConfigSetting).filter_by(key=key).first()
                if setting and setting.value:
                    results["pass"].append(f"   ✅ {key} present in DB")
                else:
                    results["warn"].append(f"   ⚠️ {key} not in DB (using .env fallback)")
        else:
            results["warn"].append("⚠️ config_settings is empty — run seed or save settings from UI")

        # 4. Instruments
        from app.database.connection import Instrument
        inst_count = session.query(Instrument).count()
        if inst_count > 0:
            results["pass"].append(f"✅ instruments: {inst_count} entries")
        else:
            results["warn"].append("⚠️ instruments table is empty — seed or use SDK search")

        # 5. Trade logs
        from app.database.connection import TradeLog
        trade_count = session.query(TradeLog).count()
        results["pass"].append(f"✅ trade_logs: {trade_count} entries")

        # 6. Market ticks
        from app.database.connection import MarketTick
        tick_count = session.query(MarketTick).count()
        if tick_count > 10000:
            results["warn"].append(f"⚠️ market_ticks has {tick_count} rows — consider cleanup")
        else:
            results["pass"].append(f"✅ market_ticks: {tick_count} entries")

    except Exception as e:
        results["fail"].append(f"❌ Query failed: {e}")
    finally:
        session.close()

    _print_results(results)
    return len(results["fail"]) == 0


def _print_results(results):
    print("\n" + "=" * 50)
    print("🗄️  DATABASE CHECK RESULTS")
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
    init_db()
    success = check_db()
    sys.exit(0 if success else 1)
