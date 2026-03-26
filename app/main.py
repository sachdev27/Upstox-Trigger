"""
Upstox Trading Automation — FastAPI Application.

Entry point: uvicorn app.main:app --reload --port 8000
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
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
from app.settings_routes import router as config_router

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

    # Load dynamic settings from DB
    from app.database.connection import get_session
    db_session = get_session()
    settings.update_from_db(db_session)
    db_session.close()
    logger.info("⚙️ Dynamic settings loaded from database.")

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

    loop = asyncio.get_running_loop()

    # --- Live Market Data & Portfolio Streamer ---
    from app.market_data.streamer import MarketDataStreamer, PortfolioStreamer
    from app.auth.service import get_auth_service
    from app.database.connection import get_session, MarketTick
    
    auth_service = get_auth_service()
    # Market streamers MUST use Live configuration (Sandbox doesn't support market data)
    streamer = MarketDataStreamer(auth_service.get_configuration(use_sandbox=False))

    async def _handle_market_tick(data):
        """
        Callback for the streamer.
        'data' is the decoded protobuf to dict from the SDK.
        """
        feeds = data.get("feeds", {})
        if not feeds:
            return

        session = get_session()
        try:
            for instrument_key, feed in feeds.items():
                # 1. Extract LTP
                # V3 uses 'fullFeed'/'marketFF'/'ltpc', V2 might use 'ff'
                ff = feed.get("fullFeed") or feed.get("ff") or {}
                market_ff = ff.get("marketFF") or {}
                ltpc = market_ff.get("ltpc") or {}
                
                ltp = ltpc.get("ltp")
                if not ltp:
                    # Try OHLC mode (V3 'marketOHLC', V2 'market_ohlc')
                    ohlc_data = market_ff.get("marketOHLC") or market_ff.get("market_ohlc") or {}
                    ltp = ohlc_data.get("ohlc", [{}])[0].get("close")
                
                if ltp:
                    now = datetime.now(timezone(timedelta(hours=5, minutes=30))) # IST
                    
                    # 2. Persist to DB
                    # V3 volume is 'vtt' or inside ohlc, OI is in market_ff
                    volume = market_ff.get("vtt") or market_ff.get("marketOHLC", {}).get("volume") or market_ff.get("market_ohlc", {}).get("volume")
                    oi = market_ff.get("oi") or market_ff.get("marketOHLC", {}).get("oi") or market_ff.get("market_ohlc", {}).get("oi")
                    
                    tick = MarketTick(
                        instrument_key=instrument_key,
                        timestamp=now,
                        last_price=ltp,
                        volume=int(volume) if volume else 0,
                        oi=float(oi) if oi else 0.0
                    )
                    session.add(tick)
                    
                    # 3. Broadcast to frontend for chart
                    # Lightweight Charts update needs { time: unix_timestamp_seconds, open, high, low, close }
                    # We'll use a simplified tick update where OHLC is just LTP
                    # The frontend's Lighthouse Charts will handle merging this into the current bar
                    
                    # Add IST offset for chart visualization if necessary (matching app.js)
                    ds = (now.timestamp()) + 19800
                    
                    msg = {
                        "type": "market_data",
                        "data": {
                            "instrument_key": instrument_key,
                            "candle": {
                                "time": int(ds),
                                "open": ltp,
                                "high": ltp,
                                "low": ltp,
                                "close": ltp
                            }
                        }
                    }
                    await broadcast_to_clients(msg)
            
            session.commit()
        except Exception as e:
            logger.error(f"Error in _handle_market_tick: {e}")
            session.rollback()
        finally:
            session.close()

    # We need to run the streamer's event loop in a way that doesn't block FastAPI
    # The SDK streamer.connect() is often blocking or starts its own thread.
    # To be safe with FastAPI's async loop, we can wrap the callback
    def sync_on_tick(message):
        # Schedule the async broadcast in the main loop
        asyncio.run_coroutine_threadsafe(_handle_market_tick(message), loop)

    streamer.on_tick = sync_on_tick
    
    # Start streaming for some defaults (In a real app, this would be dynamic based on user watchlist)
    default_instruments = [settings.NIFTY, settings.BANKNIFTY]
    # Start Portfolio Streamer
    # Portfolio streamers MUST use Live configuration for notifications
    portfolio_streamer = PortfolioStreamer(auth_service.get_configuration(use_sandbox=False))
    
    def sync_portfolio_update(message):
        # Broadcast portfolio updates to all WS clients
        asyncio.run_coroutine_threadsafe(
            broadcast_to_clients({"type": "portfolio_update", "data": message}), 
            loop
        )
        
    portfolio_streamer.on_update = sync_portfolio_update
    
    try:
        portfolio_streamer.start(order_update=True, position_update=True, holding_update=True)
        app.state.portfolio_streamer = portfolio_streamer
        logger.info("📡 Portfolio Data Streamer started.")
    except Exception as e:
        logger.error(f"❌ Failed to start portfolio streamer: {e}")

    yield
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    scheduler.stop()
    if hasattr(app.state, "market_streamer"):
        app.state.market_streamer.stop()
    if hasattr(app.state, "portfolio_streamer"):
        app.state.portfolio_streamer.stop()


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
app.include_router(config_router)

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


@app.get("/callback/")
@app.get("/callback")
async def root_callback(code: str):
    """
    Catch the precise Upstox REDIRECT_URI 'http://localhost:8210/callback/'.
    Forwards the OAuth exchange to our Auth Service.
    """
    from app.auth.service import get_auth_service
    from fastapi.responses import RedirectResponse
    
    auth = get_auth_service()
    token = auth.handle_callback(code)
    return RedirectResponse(url="/dashboard")


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
