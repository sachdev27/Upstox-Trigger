from fastapi import APIRouter, Body
from pydantic import BaseModel
from pathlib import Path
from app.config import get_settings
from app.database.connection import get_session, ConfigSetting

router = APIRouter(prefix="/settings", tags=["Settings"])

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

class SettingsUpdate(BaseModel):
    API_KEY: str | None = None
    API_SECRET: str | None = None
    REDIRECT_URI: str | None = None
    MAX_RISK_PER_TRADE_PCT: float | None = None
    MAX_DAILY_LOSS_PCT: float | None = None
    MAX_CONCURRENT_POSITIONS: int | None = None

@router.get("/")
async def get_current_settings():
    """Return non-sensitive settings and masked sensitive settings from DB."""
    settings = get_settings()
    
    # Refresh settings from DB to ensure UI is accurate
    session = get_session()
    settings.update_from_db(session)
    session.close()
    
    # Mask the secrets for UI display
    masked_key = f"{settings.API_KEY[:6]}...{settings.API_KEY[-4:]}" if len(settings.API_KEY) > 10 else settings.API_KEY
    masked_secret = "********" if settings.API_SECRET else ""
    
    return {
        "API_KEY": masked_key,
        "API_SECRET": masked_secret,
        "REDIRECT_URI": settings.REDIRECT_URI,
        "MAX_RISK_PER_TRADE_PCT": settings.MAX_RISK_PER_TRADE_PCT,
        "MAX_DAILY_LOSS_PCT": settings.MAX_DAILY_LOSS_PCT,
        "MAX_CONCURRENT_POSITIONS": settings.MAX_CONCURRENT_POSITIONS
    }

@router.post("/")
async def update_settings(updates: SettingsUpdate = Body(...)):
    """Update settings in the database."""
    session = get_session()
    updated = False
    
    for key, value in updates.dict(exclude_none=True).items():
        if value is not None:
            # Masked placeholder means do not overwrite
            if key in ["API_KEY", "API_SECRET"] and ("*" in str(value) or "..." in str(value)):
                continue
                
            # Update or create in DB
            setting = session.query(ConfigSetting).filter_by(key=key).first()
            if setting:
                setting.value = str(value)
            else:
                setting = ConfigSetting(key=key, value=str(value), category="GENERAL")
                session.add(setting)
            updated = True
            
    if updated:
        session.commit()
        # Refresh the singleton
        get_settings().update_from_db(session)
    
    session.close()
    return {"status": "success", "message": "Settings updated in database successfully"}
