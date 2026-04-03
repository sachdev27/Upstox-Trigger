"""
Startup: Database initialization and seeding.
"""

import logging

from app.config import get_settings
from app.database.connection import init_db

logger = logging.getLogger(__name__)


async def startup_database():
    """Initialize DB tables and auto-seed on first run."""
    settings = get_settings()

    init_db()
    logger.info("✅ Database initialized.")

    # Auto-seed from .env on first startup (if config_settings table is empty)
    from app.database.connection import get_session, ConfigSetting

    session = get_session()
    setting_count = session.query(ConfigSetting).count()
    session.close()
    if setting_count == 0:
        logger.info("🌱 First startup detected — seeding settings from .env...")
        from app.database.seed import seed_settings
        seed_settings()

    # Seed watchlist with Nifty 50 if empty
    from app.database.seed import seed_watchlist_nifty50
    seed_watchlist_nifty50()

    # Load dynamic settings from DB → overrides .env defaults
    settings.load_from_db()
    logger.info("⚙️ Dynamic settings loaded from database.")
