"""
Centralized configuration — single source of truth for all settings.

Priority: DB (config_settings table) → .env file → defaults
The .env file is used ONLY for initial seeding on first run.
"""

import logging
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Upstox API ──────────────────────────────────────────────
    API_VERSION: str = "2.0"
    API_KEY: str = ""
    API_SECRET: str = ""
    REDIRECT_URI: str = "http://localhost:8210/callback/"
    AUTH_CODE: str = ""
    ACCESS_TOKEN: str = ""
    ALGO_NAME: str = ""
    ALGO_ID: str = ""
    ORDER_API_VERSION: str = "3.0"
    REQUIRE_ALGO_NAME_FOR_LIVE_ORDERS: bool = True
    AUTO_SLICE_ORDERS: bool = True
    DEFAULT_MARKET_PROTECTION: int = -1

    # -- Upstox Sandbox ------------------------------------------
    USE_SANDBOX: bool = False
    SANDBOX_API_KEY: str = ""
    SANDBOX_API_SECRET: str = ""
    SANDBOX_ACCESS_TOKEN: str = ""

    # -- Network / Proxy -----------------------------------------
    # Applied to Upstox SDK clients via `upstox_client.Configuration.proxy`.
    # Example: "http://user:pass@140.245.243.157:3128"
    # Some SDK/urllib3 combinations may not support SOCKS directly.
    UPSTOX_PROXY_URL: str = ""
    APPLY_UPSTOX_SDK_PROXY: bool = False
    REQUIRE_UPSTOX_PROXY: bool = False
    APPLY_PROCESS_PROXY_ENV: bool = False

    # Optional proxies for direct `requests` calls (e.g., diagnostics).
    # Example: "socks5h://user:pass@140.245.243.157:1080"
    REQUESTS_HTTP_PROXY: str = ""
    REQUESTS_HTTPS_PROXY: str = ""

    # ── Instruments (shortcuts) ─────────────────────────────────
    BANKNIFTY: str = "NSE_INDEX|Nifty Bank"
    NIFTY: str = "NSE_INDEX|Nifty 50"

    # ── Database ────────────────────────────────────────────────
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'trading.db'}"
    # When True, values from .env (or process env) are kept and DB overrides are skipped.
    ENV_OVERRIDE_DB: bool = False

    # ── Server ──────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # ── Risk Management ─────────────────────────────────────────
    MAX_RISK_PER_TRADE_PCT: float = 1.0
    MAX_DAILY_LOSS_PCT: float = 3.0
    MAX_CONCURRENT_POSITIONS: int = 5
    SQUARE_OFF_TIME: str = "15:15"

    # ── Engine Defaults ─────────────────────────────────────────
    TRADING_CAPITAL: float = 100000.0
    PAPER_TRADING: bool = True
    TRADING_SIDE: str = "BOTH"
    MAX_OPEN_TRADES: int = 3

    # ── Strategy Persistence ────────────────────────────────────
    ACTIVE_STRATEGY_CLASS: str = "SuperTrendPro"
    ACTIVE_STRATEGY_NAME: str = "SuperTrend Pro v6.3"

    # ── Notifications ───────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Email (SMTP)
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_RECIPIENT: str = ""                       # Comma-separated list
    NOTIFICATION_CHANNELS: str = "EMAIL"            # Enabled channels (comma-separated)

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def load_from_db(self):
        """
        Override in-memory settings with values from the DB.
        Called on startup and after any settings change.

        When ENV_OVERRIDE_DB is enabled, we still backfill auth tokens from DB
        if they are currently blank in memory. This avoids websocket/auth 401s
        after restarts when tokens are intentionally not stored in .env.
        """
        try:
            from sqlalchemy import inspect
            from app.database.connection import get_session, ConfigSetting

            session = get_session()
            inspector = inspect(session.get_bind())
            if "config_settings" not in inspector.get_table_names():
                session.close()
                return

            db_settings = session.query(ConfigSetting).all()

            token_backfill_keys = {"ACCESS_TOKEN", "SANDBOX_ACCESS_TOKEN", "AUTH_CODE"}
            env_override = bool(self.ENV_OVERRIDE_DB)
            if env_override:
                logger.info("ENV_OVERRIDE_DB=True: applying DB backfill only for blank auth token fields.")

            for s in db_settings:
                key_name = str(s.key)
                if not hasattr(self, key_name):
                    continue

                # Respect env/process values when override is enabled, except
                # for blank token fields which must be backfilled from DB.
                if env_override and key_name not in token_backfill_keys:
                    continue

                current_val = getattr(self, key_name)
                if env_override and key_name in token_backfill_keys and str(current_val or "").strip():
                    continue

                attr_type = type(current_val)
                try:
                    if attr_type == bool:
                        val = str(s.value).lower() in ("true", "1", "yes")
                    else:
                        val = attr_type(s.value)
                    setattr(self, key_name, val)
                except (ValueError, TypeError):
                    logger.warning(f"Failed to convert DB setting {key_name}='{s.value}' to {attr_type}")
            session.close()
        except Exception as e:
            logger.warning(f"Could not load settings from DB: {e}")

    def save_to_db(self, key: str, value: str, category: str = "GENERAL", is_secret: bool = False):
        """
        Save a single setting to the database.
        This is the canonical way to persist a configuration change.
        """
        try:
            from app.database.connection import get_session, ConfigSetting
            from datetime import datetime

            session = get_session()
            existing = session.query(ConfigSetting).filter_by(key=key).first()
            if existing:
                existing.value = str(value)
                existing.updated_at = datetime.utcnow()
            else:
                setting = ConfigSetting(
                    key=key,
                    value=str(value),
                    category=category,
                    is_secret=is_secret,
                    description=f"Set via API"
                )
                session.add(setting)
            session.commit()
            session.close()

            # Update in-memory
            if hasattr(self, key):
                attr_type = type(getattr(self, key))
                if attr_type == bool:
                    setattr(self, key, str(value).lower() in ("true", "1", "yes"))
                else:
                    setattr(self, key, attr_type(value))
        except Exception as e:
            logger.error(f"Failed to save setting {key} to DB: {e}")


# Module-level singleton (NO @lru_cache — we need mutability)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the Settings singleton. NOT cached — DB can update it."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
