"""One-time DB cleanup: sync credentials, set risk params, clear stale proxy settings."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from datetime import datetime
from app.database.connection import get_session, ConfigSetting


def upsert(db, key, value):
    """Insert or update a config setting."""
    row = db.query(ConfigSetting).filter(ConfigSetting.key == key).first()
    if row:
        old = row.value
        row.value = str(value)
        row.updated_at = datetime.utcnow()
        print(f"  UPDATED  {key:30s}  {old!r:>30s} -> {value!r}")
    else:
        row = ConfigSetting(key=key, value=str(value), category="GENERAL")
        db.add(row)
        print(f"  CREATED  {key:30s}  -> {value!r}")


def main():
    db = get_session()

    print("=" * 70)
    print("  STEP 1: Sync API credentials from .env to DB")
    print("=" * 70)
    upsert(db, "API_KEY", os.environ.get("API_KEY", ""))
    upsert(db, "API_SECRET", os.environ.get("API_SECRET", ""))
    upsert(db, "REDIRECT_URI", os.environ.get("REDIRECT_URI", "http://localhost:8210/callback/"))
    upsert(db, "API_VERSION", os.environ.get("API_VERSION", "2.0"))
    # Keep ACCESS_TOKEN — it's a live JWT that doesn't belong in .env
    # Keep AUTH_CODE — it's transient from OAuth flow

    print()
    print("=" * 70)
    print("  STEP 2: Set risk management parameters")
    print("=" * 70)
    upsert(db, "TRADING_CAPITAL", "100000.0")
    upsert(db, "MAX_DAILY_LOSS_PCT", "3.0")
    upsert(db, "MAX_RISK_PER_TRADE_PCT", "1.0")
    upsert(db, "MAX_CONCURRENT_POSITIONS", "5")

    print()
    print("=" * 70)
    print("  STEP 3: Clean up proxy settings to match .env")
    print("=" * 70)
    upsert(db, "REQUIRE_UPSTOX_PROXY", "False")
    upsert(db, "APPLY_PROCESS_PROXY_ENV", "False")
    # Clear stale proxy URLs from DB so they don't override .env
    for proxy_key in ["UPSTOX_PROXY_URL", "REQUESTS_HTTPS_PROXY", "REQUESTS_HTTP_PROXY"]:
        row = db.query(ConfigSetting).filter(ConfigSetting.key == proxy_key).first()
        if row:
            old = row.value
            row.value = ""
            row.updated_at = datetime.utcnow()
            print(f"  CLEARED  {proxy_key:30s}  was: {old[:20]}...")
        else:
            print(f"  OK       {proxy_key:30s}  (not in DB)")

    print()
    print("=" * 70)
    print("  STEP 4: Confirm trading mode")
    print("=" * 70)
    upsert(db, "PAPER_TRADING", "False")
    upsert(db, "USE_SANDBOX", "False")
    upsert(db, "ENV_OVERRIDE_DB", "True")

    db.commit()
    print()
    print("=" * 70)
    print("  ✅ DB synced. Restart the app to apply.")
    print("=" * 70)
    db.close()


if __name__ == "__main__":
    main()
