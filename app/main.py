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
from app.monitoring.routes import router as monitoring_router
from app.notifications.routes import router as notification_router

logger = logging.getLogger(__name__)

# Track WebSocket clients and their specific instrument interests
ws_clients: set[WebSocket] = set()
# instrument_key -> set of WebSockets
instrument_subscriptions: dict[str, set[WebSocket]] = {}
# WebSocket -> set of instrument_keys
client_subscriptions: dict[WebSocket, set[str]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
    logger.info("🚀 Starting Upstox Trading Automation...")
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

    # Load dynamic settings from DB → overrides .env defaults
    settings.load_from_db()
    logger.info("⚙️ Dynamic settings loaded from database.")

    # Auto-initialize engine services
    from app.engine import get_engine
    engine = get_engine()
    engine.initialize()

    # Start the scheduler
    from app.scheduler.service import SchedulerService
    
    scheduler = SchedulerService()
    engine.broadcast_callback = broadcast_to_clients

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
    
    # Always subscribe to core indices for the status bar
    try:
        streamer.start(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"])
        logger.info("📡 Core indices (Nifty/Bank Nifty) subscribed on startup (V3).")
    except Exception as e:
        logger.error(f"Failed to subscribe to core indices on startup: {e}")

    async def _handle_market_tick(data):
        """
        Callback for the streamer.
        'data' is the decoded protobuf to dict from the SDK.
        """
        if not data:
            return
            
        if not isinstance(data, dict):
            return
            
        # SDK V3 usually passes a dict with 'feeds'
        feeds = data.get("feeds", {})
        if not feeds:
            # Fallback: Check if the top-level keys look like instrument keys (e.g. "NSE_EQ|...")
            if any("|" in k for k in data.keys()):
                feeds = data
            else:
                return

        try:
            for instrument_key, feed in feeds.items():
                # V3 structure from Upstox is nested. Let's try to find ltp anywhere.
                # Common paths:
                # Stock: feed['fullFeed']['marketFF']['ltpc']['ltp']
                # Index: feed['fullFeed']['indexFF']['ltpc']['ltp']
                # LTQ: feed['ltpc']['ltp'] (some modes)
                
                # 1. Fast inner extraction
                inner = feed.get("fullFeed") or feed.get("ff") or feed
                if "marketFF" in inner: inner = inner["marketFF"]
                elif "indexFF" in inner: inner = inner["indexFF"]
                
                # 2. Direct extraction with early exit
                ltpc = inner.get("ltpc", {})
                ltp = ltpc.get("ltp")
                if not ltp:
                    continue
                
                # 3. Optimized metadata extraction
                greeks = inner.get("optionGreeks", {})
                volume = inner.get("vtt", 0)
                iv = inner.get("iv", greeks.get("iv", 0.0))
                
                ds = datetime.now(timezone(timedelta(hours=5, minutes=30))).timestamp() # IST timestamp
                # 4. Packed Array Transport (Binary-lite)
                # Format: ["t", key, ltp, v, iv, delta, theta, ts]
                msg = [
                    "t", 
                    instrument_key, 
                    float(ltp), 
                    int(volume), 
                    round(float(iv or 0.0) * 100, 2),
                    round(float(greeks.get("delta") or 0.0), 4),
                    round(float(greeks.get("theta") or 0.0), 2),
                    int(ds)
                ]
                
                target_clients = instrument_subscriptions.get(instrument_key, set())
                for client_ws in list(target_clients):
                    try:
                        await client_ws.send_json(msg)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error in _handle_market_tick: {e}")

    # We need to run the streamer's event loop in a way that doesn't block FastAPI
    # The SDK streamer.connect() is often blocking or starts its own thread.
    # To be safe with FastAPI's async loop, we can wrap the callback
    def sync_on_tick(message):
        # Schedule the async broadcast in the main loop
        asyncio.run_coroutine_threadsafe(_handle_market_tick(message), loop)

    streamer.on_tick = sync_on_tick
    
    # Initialize market data streamer (dynamic subscriptions started from frontend)
    try:
        streamer.start([])
        app.state.market_streamer = streamer
        logger.info("📡 Market Data Streamer started (Ready for dynamic subscriptions)")
    except Exception as e:
        logger.error(f"❌ Failed to start market streamer: {e}")

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
        logger.error(f"❌ Failed to start market streamer: {e}")

    # --- Heartbeat & Periodic Status ---
    async def _periodic_updates():
        while True:
            try:
                # Send a heartbeat every 10 seconds to keep WS alive and show it's working
                await broadcast_to_clients({
                    "type": "heartbeat", 
                    "data": {"timestamp": datetime.now().isoformat()}
                })
                # Refresh status too
                from app.engine import get_engine
                engine = get_engine()
                await broadcast_to_clients({"type": "status", "data": engine.get_status()})
            except Exception:
                pass
            await asyncio.sleep(10)

    asyncio.create_task(_periodic_updates())

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
app.include_router(monitoring_router)
app.include_router(notification_router)

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
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                key = msg.get("instrument_key")
                
                if action == "get_status":
                    from app.engine import get_engine
                    status = get_engine().get_status()
                    await ws.send_json({"type": "status", "data": status})
                
                elif action == "subscribe" and key:
                    keys_to_sub = [k.strip() for k in key.split(',') if k.strip()]
                    for k in keys_to_sub:
                        if k not in instrument_subscriptions:
                            instrument_subscriptions[k] = set()
                        instrument_subscriptions[k].add(ws)
                        
                        if ws not in client_subscriptions:
                            client_subscriptions[ws] = set()
                        client_subscriptions[ws].add(k)
                        
                        if len(instrument_subscriptions[k]) == 1:
                            if hasattr(app.state, "market_streamer") and app.state.market_streamer:
                                try:
                                    # Use mode='full' to get Greeks and Volume as per V3 docs
                                    app.state.market_streamer.subscribe([k], mode="full")
                                    logger.info(f"📡 SDK Subscription started (FULL mode) for: {k}")
                                except Exception as e:
                                    logger.error(f"Failed to subscribe to {k}: {e}")
                
                elif action == "unsubscribe" and key:
                    keys_to_unsub = [k.strip() for k in key.split(',') if k.strip()]
                    for k in keys_to_unsub:
                        if k in instrument_subscriptions:
                            instrument_subscriptions[k].discard(ws)
                            if ws in client_subscriptions:
                                client_subscriptions[ws].discard(k)
                                
                            if len(instrument_subscriptions[k]) == 0:
                                del instrument_subscriptions[k]
                                if hasattr(app.state, "market_streamer") and app.state.market_streamer:
                                    try:
                                        app.state.market_streamer.unsubscribe([k])
                                        logger.info(f"🛑 SDK Subscription stopped for: {k}")
                                    except Exception as e:
                                        logger.error(f"Failed to unsubscribe from {k}: {e}")
                                
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        # Complete cleanup for this client
        keys = client_subscriptions.pop(ws, set())
        for key in keys:
            if key in instrument_subscriptions:
                instrument_subscriptions[key].discard(ws)
                if len(instrument_subscriptions[key]) == 0:
                    del instrument_subscriptions[key]
                    if hasattr(app.state, "market_streamer") and app.state.market_streamer:
                        try:
                            app.state.market_streamer.unsubscribe([key])
                            logger.info(f"🛑 SDK Subscription stopped for: {key} (Client disconnected)")
                        except Exception as e:
                            logger.error(f"Failed to unsubscribe from {key} during disconnect: {e}")
        
        ws_clients.discard(ws)
        logger.info(f"WebSocket client disconnected. Total: {len(ws_clients)}")


async def broadcast_to_clients(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    global ws_clients
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
    target_token = auth.settings.SANDBOX_ACCESS_TOKEN if auth.settings.USE_SANDBOX else auth.settings.ACCESS_TOKEN
    token_valid = not auth._is_token_expired(target_token)

    return {
        "status": "healthy",
        "auth": "valid" if token_valid else "expired",
        "api_version": get_settings().API_VERSION,
    }
