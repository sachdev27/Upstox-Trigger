"""
Centralized configuration — single source of truth for all settings.
Loads from .env file via pydantic-settings.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Upstox API ──────────────────────────────────────────────
    API_VERSION: str = "2.0"
    API_KEY: str = ""
    API_SECRET: str = ""
    REDIRECT_URI: str = "http://localhost:8210/callback/"
    AUTH_CODE: str = ""
    ACCESS_TOKEN: str = ""

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


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
