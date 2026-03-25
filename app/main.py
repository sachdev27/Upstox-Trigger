"""
Upstox Trading Automation — FastAPI Application.

Entry point: uvicorn app.main:app --reload --port 8000
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.config import get_settings, BASE_DIR
from app.database.connection import init_db
from app.auth.routes import router as auth_router
from app.market_data.routes import router as market_router
from app.orders.routes import router as orders_router
from app.strategies.routes import router as strategies_router
from app.engine_routes import router as engine_router

logger = logging.getLogger(__name__)

# Track WebSocket clients for live updates
ws_clients: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
    logger.info("🚀 Starting Upstox Trading Automation...")
    init_db()
    logger.info("✅ Database initialized.")

    # Start the scheduler
    from app.scheduler.service import SchedulerService
    from app.engine import get_engine
    
    scheduler = SchedulerService()
    engine = get_engine()

    async def _scheduled_run_cycle():
        """Called automatically every minute during market hours."""
        if engine.auto_mode:
            logger.info("🤖 [AUTO MODE] Running scheduled cycle...")
            await engine.run_cycle()
            # Broadcast latest status to frontend
            await broadcast_to_clients({"type": "status", "data": engine.get_status()})

    scheduler.on("candle_check", _scheduled_run_cycle)
    scheduler.start()
    app.state.scheduler = scheduler

    yield
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    scheduler.stop()


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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(market_router)
app.include_router(orders_router)
app.include_router(strategies_router)
app.include_router(engine_router)

# ── Static files (frontend) ────────────────────────────────────
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


# ── Frontend serving ───────────────────────────────────────────

@app.get("/dashboard", tags=["Frontend"])
async def serve_dashboard():
    """Serve the main dashboard page."""
    index = frontend_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"error": "Frontend not built. Place files in /frontend/"}


# ── WebSocket for live updates ──────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await ws.accept()
    ws_clients.add(ws)
    logger.info(f"WebSocket client connected. Total: {len(ws_clients)}")

    try:
        while True:
            # Keep connection alive and listen for client messages
            data = await ws.receive_text()
            # Client can send commands like {"action": "get_status"}
            try:
                msg = json.loads(data)
                if msg.get("action") == "get_status":
                    from app.engine import get_engine
                    status = get_engine().get_status()
                    await ws.send_json({"type": "status", "data": status})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_clients.discard(ws)
        logger.info(f"WebSocket client disconnected. Total: {len(ws_clients)}")


async def broadcast_to_clients(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    ws_clients -= dead


# ── API Endpoints ───────────────────────────────────────────────

@app.get("/", tags=["Root"])
async def root():
    """API health check."""
    return {
        "name": "Upstox Trading Automation",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "dashboard": "/dashboard",
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
