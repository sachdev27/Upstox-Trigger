"""
Centralized configuration — single source of truth for all settings.
Loads from .env file via pydantic-settings.
"""

import logging
from functools import lru_cache
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
    
    # -- Upstox Sandbox ------------------------------------------
    USE_SANDBOX: bool = False
    SANDBOX_API_KEY: str = ""
    SANDBOX_API_SECRET: str = ""
    SANDBOX_ACCESS_TOKEN: str = ""

    # ── Instruments (shortcuts) ─────────────────────────────────
    BANKNIFTY: str = "NSE_INDEX|Nifty Bank"
    NIFTY: str = "NSE_INDEX|Nifty 50"

    # ── Database ────────────────────────────────────────────────
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'trading.db'}"

    # ── Server ──────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # ── Risk Management ─────────────────────────────────────────
    MAX_RISK_PER_TRADE_PCT: float = 1.0       # % of equity risked per trade
    MAX_DAILY_LOSS_PCT: float = 3.0           # auto-stop if daily loss exceeds
    MAX_CONCURRENT_POSITIONS: int = 5
    SQUARE_OFF_TIME: str = "15:15"            # IST, for intraday strategies

    # ── Notifications ───────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def update_from_db(self, session):
        """
        Refresh settings from the database ConfigSetting table.
        This allows hot-swapping settings without a restart.
        """
        from sqlalchemy import inspect
        
        # We use a late import of our model to avoid circularities
        # and check if the table exists first (for initial setup)
        inspector = inspect(session.get_bind())
        if "config_settings" not in inspector.get_table_names():
            return

        from app.database.connection import ConfigSetting
        
        db_settings = session.query(ConfigSetting).all()
        for s in db_settings:
            if hasattr(self, s.key):
                # Type conversion based on default field type
                attr_type = type(getattr(self, s.key))
                try:
                    if attr_type == bool:
                        val = s.value.lower() in ("true", "1", "yes")
                    else:
                        val = attr_type(s.value)
                    setattr(self, s.key, val)
                except (ValueError, TypeError):
                    logger.warning(f"Failed to convert DB setting {s.key}='{s.value}' to {attr_type}")


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
