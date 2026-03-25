"""
Database Connection — SQLAlchemy setup for SQLite (dev) / PostgreSQL (prod).
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

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
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database tables initialized.")
