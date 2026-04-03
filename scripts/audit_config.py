"""Audit which API keys, tokens, and settings are active at runtime."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=False)

from app.database.connection import get_session, ConfigSetting
from app.config import Settings


def mask(key, val):
    if not val or val in ('(NOT IN DB)', '(NOT SET)', ''):
        return val or '(empty)'
    if any(s in key.upper() for s in ['SECRET', 'PASSWORD', 'TOKEN', 'AUTH_CODE']):
        if len(val) > 8:
            return val[:4] + '****' + val[-4:]
        return '****'
    if 'API_KEY' in key.upper():
        if len(val) > 12:
            return val[:8] + '****' + val[-4:]
    return val


KEYS = [
    'API_KEY', 'API_SECRET', 'ACCESS_TOKEN', 'AUTH_CODE',
    'SANDBOX_API_KEY', 'SANDBOX_API_SECRET', 'SANDBOX_ACCESS_TOKEN',
    'USE_SANDBOX', 'PAPER_TRADING',
    'REDIRECT_URI', 'API_VERSION',
    'REQUIRE_UPSTOX_PROXY', 'APPLY_UPSTOX_SDK_PROXY',
    'UPSTOX_PROXY_URL', 'REQUESTS_HTTPS_PROXY', 'REQUESTS_HTTP_PROXY',
    'APPLY_PROCESS_PROXY_ENV',
    'ALGO_NAME', 'ALGO_ID',
    'TRADING_CAPITAL', 'TRADING_SIDE',
    'ENV_OVERRIDE_DB',
]


def main():
    db = get_session()

    print("=" * 65)
    print("  DATABASE CONFIG (highest priority at runtime)")
    print("=" * 65)
    for key in KEYS:
        row = db.query(ConfigSetting).filter(ConfigSetting.key == key).first()
        val = row.value if row else '(NOT IN DB)'
        print(f"  {key:30s} = {mask(key, val)}")

    print()
    print("=" * 65)
    print("  .ENV FILE (used only if DB has no value)")
    print("=" * 65)
    for key in KEYS:
        val = os.environ.get(key, '(NOT SET)')
        print(f"  {key:30s} = {mask(key, val)}")

    print()
    print("=" * 65)
    print("  EFFECTIVE (what auth service actually resolves)")
    print("=" * 65)
    settings = Settings()
    settings.load_from_db()

    use_sandbox = settings.USE_SANDBOX
    paper = settings.PAPER_TRADING
    api_key = settings.SANDBOX_API_KEY if use_sandbox else settings.API_KEY
    api_secret = settings.SANDBOX_API_SECRET if use_sandbox else settings.API_SECRET
    token = settings.SANDBOX_ACCESS_TOKEN if use_sandbox else settings.ACCESS_TOKEN

    print(f"  USE_SANDBOX               = {use_sandbox}")
    print(f"  PAPER_TRADING             = {paper}")
    print(f"  Mode                      = {'SANDBOX' if use_sandbox else 'LIVE'} / {'PAPER' if paper else 'REAL ORDERS'}")
    print(f"  Active API_KEY            = {mask('API_KEY', api_key or '')}")
    print(f"  Active API_SECRET         = {mask('API_SECRET', api_secret or '')}")
    print(f"  Active ACCESS_TOKEN       = {mask('ACCESS_TOKEN', token or '')}")
    print(f"  Token present?            = {bool(token and len(str(token)) > 10)}")
    print(f"  REDIRECT_URI              = {settings.REDIRECT_URI}")
    print(f"  ALGO_NAME                 = {getattr(settings, 'ALGO_NAME', '(not set)')}")
    print(f"  REQUIRE_UPSTOX_PROXY      = {settings.REQUIRE_UPSTOX_PROXY}")
    print(f"  APPLY_UPSTOX_SDK_PROXY    = {getattr(settings, 'APPLY_UPSTOX_SDK_PROXY', '(not set)')}")

    # Check for conflicts
    print()
    print("=" * 65)
    print("  CONFLICT CHECK")
    print("=" * 65)

    db_api_key_row = db.query(ConfigSetting).filter(ConfigSetting.key == 'API_KEY').first()
    env_api_key = os.environ.get('API_KEY', '')
    db_api_key = db_api_key_row.value if db_api_key_row else ''

    if db_api_key and env_api_key and db_api_key != env_api_key:
        print(f"  !! API_KEY MISMATCH: .env and DB have DIFFERENT values")
        print(f"     .env = {mask('API_KEY', env_api_key)}")
        print(f"     DB   = {mask('API_KEY', db_api_key)}")
        print(f"     Winner: DB (DB always wins unless ENV_OVERRIDE_DB=True)")
    elif db_api_key and env_api_key and db_api_key == env_api_key:
        print(f"  OK: API_KEY same in .env and DB")
    elif db_api_key and not env_api_key:
        print(f"  INFO: API_KEY only in DB (not in .env)")
    elif env_api_key and not db_api_key:
        print(f"  INFO: API_KEY only in .env (not in DB)")

    db_token_row = db.query(ConfigSetting).filter(ConfigSetting.key == 'ACCESS_TOKEN').first()
    db_token = db_token_row.value if db_token_row else ''
    if not db_token or len(db_token) < 10:
        print(f"  !! ACCESS_TOKEN in DB is empty/missing — auth may fail for live calls")
    else:
        print(f"  OK: ACCESS_TOKEN present in DB ({len(db_token)} chars)")

    if not paper and not use_sandbox:
        print(f"  !! WARNING: LIVE MODE + REAL ORDERS — trades will use real money!")

    db.close()


if __name__ == '__main__':
    main()
