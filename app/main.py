"""
Upstox Trading Automation — FastAPI Application.

Entry point: uvicorn app.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database.connection import init_db
from app.auth.routes import router as auth_router
from app.market_data.routes import router as market_router
from app.orders.routes import router as orders_router
from app.strategies.routes import router as strategies_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
    logger.info("🚀 Starting Upstox Trading Automation...")
    init_db()
    logger.info("✅ Database initialized.")
    yield
    # Shutdown
    logger.info("🛑 Shutting down...")


app = FastAPI(
    title="Upstox Trading Automation",
    description=(
        "Automated trading platform powered by Upstox API v2. "
        "Runs strategies like SuperTrend Pro v6.3, manages orders, "
        "and provides real-time market data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(market_router)
app.include_router(orders_router)
app.include_router(strategies_router)


@app.get("/", tags=["Root"])
async def root():
    """API health check."""
    return {
        "name": "Upstox Trading Automation",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Root"])
async def health():
    """Detailed health check."""
    from app.auth.service import get_auth_service

    auth = get_auth_service()
    token_valid = not auth._is_token_expired(auth.settings.ACCESS_TOKEN)

    return {
        "status": "healthy",
        "auth": "valid" if token_valid else "expired",
        "api_version": get_settings().API_VERSION,
    }
