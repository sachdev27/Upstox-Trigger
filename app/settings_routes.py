"""
Settings API routes — read and write application configuration.

All settings are persisted to the database (config_settings table).
"""

from fastapi import APIRouter, Body
from pydantic import BaseModel
from app.config import get_settings
from app.database.connection import get_session, ConfigSetting
from app.engine import get_engine

router = APIRouter(prefix="/settings", tags=["Settings"])


class SettingsUpdate(BaseModel):
    API_KEY: str | None = None
    API_SECRET: str | None = None
    REDIRECT_URI: str | None = None
    ALGO_NAME: str | None = None
    ALGO_ID: str | None = None
    UPSTOX_PROXY_URL: str | None = None
    REQUESTS_HTTP_PROXY: str | None = None
    REQUESTS_HTTPS_PROXY: str | None = None
    REQUIRE_UPSTOX_PROXY: bool | None = None
    MAX_RISK_PER_TRADE_PCT: float | None = None
    MAX_DAILY_LOSS_PCT: float | None = None
    MAX_CONCURRENT_POSITIONS: int | None = None
    USE_SANDBOX: bool | None = None
    SANDBOX_API_KEY: str | None = None
    SANDBOX_API_SECRET: str | None = None
    SANDBOX_ACCESS_TOKEN: str | None = None
    TRADING_CAPITAL: float | None = None
    PAPER_TRADING: bool | None = None
    TRADING_SIDE: str | None = None
    MAX_OPEN_TRADES: int | None = None
    SQUARE_OFF_TIME: str | None = None
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    SMTP_SERVER: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAIL_RECIPIENT: str | None = None
    NOTIFICATION_CHANNELS: str | None = None


# Keys that should be masked in the UI
_SECRET_KEYS = {
    "API_KEY", "API_SECRET", "SANDBOX_API_KEY", "SANDBOX_API_SECRET",
    "SANDBOX_ACCESS_TOKEN", "ACCESS_TOKEN", "SMTP_PASSWORD", "TELEGRAM_BOT_TOKEN",
    "UPSTOX_PROXY_URL", "REQUESTS_HTTP_PROXY", "REQUESTS_HTTPS_PROXY",
}

# Category mapping for DB storage
_CATEGORY_MAP = {
    "API_KEY": "API", "API_SECRET": "API", "REDIRECT_URI": "API", "ACCESS_TOKEN": "API", "AUTH_CODE": "API",
    "ALGO_NAME": "API", "ALGO_ID": "API",
    "UPSTOX_PROXY_URL": "API", "REQUESTS_HTTP_PROXY": "API", "REQUESTS_HTTPS_PROXY": "API", "REQUIRE_UPSTOX_PROXY": "API",
    "MAX_RISK_PER_TRADE_PCT": "RISK", "MAX_DAILY_LOSS_PCT": "RISK", "MAX_CONCURRENT_POSITIONS": "RISK", "SQUARE_OFF_TIME": "RISK",
    "TRADING_CAPITAL": "ENGINE", "PAPER_TRADING": "ENGINE", "TRADING_SIDE": "ENGINE", "MAX_OPEN_TRADES": "ENGINE",
    "USE_SANDBOX": "ENGINE", "SANDBOX_API_KEY": "API", "SANDBOX_API_SECRET": "API", "SANDBOX_ACCESS_TOKEN": "API",
    "TELEGRAM_BOT_TOKEN": "NOTIFICATIONS", "TELEGRAM_CHAT_ID": "NOTIFICATIONS",
    "SMTP_SERVER": "NOTIFICATIONS", "SMTP_PORT": "NOTIFICATIONS", "SMTP_USER": "NOTIFICATIONS",
    "SMTP_PASSWORD": "NOTIFICATIONS", "EMAIL_RECIPIENT": "NOTIFICATIONS", "NOTIFICATION_CHANNELS": "NOTIFICATIONS",
}


def _mask(value: str, show_chars: int = 6) -> str:
    """Mask a sensitive value for UI display."""
    if not value or len(value) <= show_chars:
        return value
    return f"{value[:show_chars]}...{value[-4:]}"


@router.get("/")
async def get_current_settings():
    """Return all settings, with sensitive values masked."""
    settings = get_settings()
    # Always refresh from DB before returning
    settings.load_from_db()

    return {
        "API_KEY": _mask(settings.API_KEY) if settings.API_KEY else "",
        "API_SECRET": "********" if settings.API_SECRET else "",
        "REDIRECT_URI": settings.REDIRECT_URI,
        "ALGO_NAME": settings.ALGO_NAME,
        "ALGO_ID": settings.ALGO_ID,
        "UPSTOX_PROXY_URL": "********" if settings.UPSTOX_PROXY_URL else "",
        "REQUESTS_HTTP_PROXY": "********" if settings.REQUESTS_HTTP_PROXY else "",
        "REQUESTS_HTTPS_PROXY": "********" if settings.REQUESTS_HTTPS_PROXY else "",
        "REQUIRE_UPSTOX_PROXY": settings.REQUIRE_UPSTOX_PROXY,
        "MAX_RISK_PER_TRADE_PCT": settings.MAX_RISK_PER_TRADE_PCT,
        "MAX_DAILY_LOSS_PCT": settings.MAX_DAILY_LOSS_PCT,
        "MAX_CONCURRENT_POSITIONS": settings.MAX_CONCURRENT_POSITIONS,
        "SQUARE_OFF_TIME": settings.SQUARE_OFF_TIME,
        "USE_SANDBOX": settings.USE_SANDBOX,
        "SANDBOX_API_KEY": _mask(settings.SANDBOX_API_KEY) if settings.SANDBOX_API_KEY else "",
        "SANDBOX_API_SECRET": "********" if settings.SANDBOX_API_SECRET else "",
        "SANDBOX_ACCESS_TOKEN": "********" if settings.SANDBOX_ACCESS_TOKEN else "",
        "TRADING_CAPITAL": settings.TRADING_CAPITAL,
        "PAPER_TRADING": settings.PAPER_TRADING,
        "TRADING_SIDE": settings.TRADING_SIDE,
        "MAX_OPEN_TRADES": settings.MAX_OPEN_TRADES,
        "TELEGRAM_BOT_TOKEN": "********" if settings.TELEGRAM_BOT_TOKEN else "",
        "TELEGRAM_CHAT_ID": settings.TELEGRAM_CHAT_ID,
        "SMTP_SERVER": settings.SMTP_SERVER,
        "SMTP_PORT": settings.SMTP_PORT,
        "SMTP_USER": settings.SMTP_USER,
        "SMTP_PASSWORD": "********" if settings.SMTP_PASSWORD else "",
        "EMAIL_RECIPIENT": settings.EMAIL_RECIPIENT,
        "NOTIFICATION_CHANNELS": settings.NOTIFICATION_CHANNELS,
    }


@router.post("/")
async def update_settings(updates: SettingsUpdate = Body(...)):
    """Update settings — persisted to the database."""
    settings = get_settings()
    updated_keys = []

    # Map the Pydantic model to a dict, excluding None values
    update_data = updates.model_dump(exclude_none=True)

    for key, value in update_data.items():
        # Skip masked placeholder values (user didn't change them)
        if key in _SECRET_KEYS and ("*" in str(value) or "..." in str(value)):
            continue

        category = _CATEGORY_MAP.get(key, "GENERAL")
        is_secret = key in _SECRET_KEYS
        settings.save_to_db(key, str(value), category=category, is_secret=is_secret)
        updated_keys.append(key)

    # Sync engine with new settings
    if updated_keys:
        if any(k in {
            "UPSTOX_PROXY_URL", "REQUESTS_HTTP_PROXY", "REQUESTS_HTTPS_PROXY", "REQUIRE_UPSTOX_PROXY"
        } for k in updated_keys):
            from app.network_proxy import configure_network_proxies
            configure_network_proxies(settings)

        engine = get_engine()
        engine.sync_from_settings()

    return {"status": "success", "message": f"Updated {len(updated_keys)} settings", "updated": updated_keys}
