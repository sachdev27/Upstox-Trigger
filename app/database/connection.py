"""
Database Connection — SQLAlchemy setup for SQLite (dev) / PostgreSQL (prod).
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

from app.config import get_settings, BASE_DIR

logger = logging.getLogger(__name__)

Base = declarative_base()


# ── Models ──────────────────────────────────────────────────────

class TradeLog(Base):
    """Record of every trade executed by the system."""

    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    strategy_name = Column(String, nullable=False)
    instrument_key = Column(String, nullable=False)
    action = Column(String, nullable=False)  # BUY / SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    order_id = Column(String)
    status = Column(String, default="pending")  # pending, filled, cancelled
    pnl = Column(Float, default=0.0)
    metadata_json = Column(JSON, default={})


class StrategyState(Base):
    """Persisted strategy configuration and state."""

    __tablename__ = "strategy_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=False)
    timeframe = Column(String, default="15m")
    instruments = Column(JSON, default=[])
    params = Column(JSON, default={})
    paper_trading = Column(Boolean, default=True)


class CandleCache(Base):
    """Cached candle data for faster strategy evaluation."""

    __tablename__ = "candle_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_key = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0)


class ConfigSetting(Base):
    """Dynamic application settings stored in the database."""

    __tablename__ = "config_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    category = Column(String, default="GENERAL")  # API, RISK, ENGINE, etc.
    description = Column(String)
    is_secret = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Instrument(Base):
    """Master list of instruments (stocks, indices, options)."""

    __tablename__ = "instruments"

    instrument_key = Column(String, primary_key=True)
    symbol = Column(String, nullable=False)
    name = Column(String, nullable=False)
    exchange = Column(String, nullable=False)
    segment = Column(String)
    lot_size = Column(Integer, default=1)
    tick_size = Column(Float, default=0.05)
    instrument_type = Column(String)  # INDEX, EQUITIES, CE, PE, FUT
    expiry = Column(String)
    strike = Column(Float)


class MarketTick(Base):
    """Real-time price ticks for instruments."""

    __tablename__ = "market_ticks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_key = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    last_price = Column(Float, nullable=False)
    volume = Column(Float)
    oi = Column(Float)


class Watchlist(Base):
    """User-defined watchlist for monitoring and strategy evaluation."""

    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_key = Column(String, unique=True, nullable=False)
    symbol = Column(String)
    name = Column(String)
    timeframes = Column(JSON, default=["15m"])  # Multi-TF scanning
    added_at = Column(DateTime, default=datetime.utcnow)


class ActiveSignal(Base):
    """Persisted strategy signals — tracks active/closed status."""

    __tablename__ = "active_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String, nullable=False)
    instrument_key = Column(String, nullable=False)
    timeframe = Column(String, default="15m")
    action = Column(String, nullable=False)  # BUY / SELL
    price = Column(Float, nullable=False)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    confidence_score = Column(Integer, default=0)
    status = Column(String, default="active")  # active / closed
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    metadata_json = Column(JSON, default={})


# ── Engine & Session ────────────────────────────────────────────

_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = settings.DATABASE_URL

        # Ensure data directory exists for SQLite
        if db_url.startswith("sqlite"):
            db_path = Path(db_url.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_engine(
            db_url,
            echo=settings.DEBUG,
            connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        )
        logger.info(f"Database engine created: {db_url}")
    return _engine


def get_session():
    """Get a new database session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    """Create all tables if they don't exist, and run lightweight migrations."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database tables initialized.")

    # ── Lightweight migrations for existing DBs ──
    from sqlalchemy import text
    with engine.connect() as conn:
        # Add 'timeframes' column to watchlists if missing
        try:
            conn.execute(text(
                "ALTER TABLE watchlists ADD COLUMN timeframes TEXT DEFAULT '[\"15m\"]'"
            ))
            conn.commit()
            logger.info("Migration: added 'timeframes' column to watchlists")
        except Exception:
            pass  # Column already exists
