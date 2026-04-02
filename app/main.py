"""
Upstox Trading Automation — FastAPI Application.

Entry point: uvicorn app.main:app --reload --port 8000
"""

import asyncio
import copy
import json
import logging
import time
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

    # Configure global proxy behavior early so all SDK/API clients inherit it.
    from app.network_proxy import configure_network_proxies
    configure_network_proxies(settings)

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

    # Seed watchlist with Nifty 50 if empty
    from app.database.seed import seed_watchlist_nifty50
    seed_watchlist_nifty50()

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
        """Called automatically every 5 seconds during market hours."""
        if engine.auto_mode:
            logger.info("🤖 [AUTO MODE] Running scheduled cycle...")
            await engine.run_cycle()
            # Broadcast latest status to frontend
            await broadcast_to_clients({"type": "status", "data": engine.get_status()})

    scheduler.on("candle_check", _scheduled_run_cycle)

    async def _scheduled_square_off():
        """Called at market close (3:30 PM IST) to force-exit all open positions."""
        logger.info("🏁 [MARKET CLOSE] Squaring off all open positions...")
        await engine.square_off_all()
        await broadcast_to_clients({"type": "status", "data": engine.get_status()})

    scheduler.on("market_close", _scheduled_square_off)
    scheduler.start()
    app.state.scheduler = scheduler

    loop = asyncio.get_running_loop()
    app.state.last_market_tick = None
    app.state.last_market_tick_epoch = 0.0
    app.state.greeks_cache = {}  # {instrument_key: {delta, theta, iv, ...}}

    # --- Live Market Data & Portfolio Streamer ---
    from app.market_data.streamer import MarketDataStreamer, PortfolioStreamer
    from app.market_data.service import MarketDataService
    from app.auth.service import get_auth_service
    from app.database.connection import get_session

    auth_service = get_auth_service()
    # Market streamers MUST use Live configuration (Sandbox doesn't support market data)
    streamer_config = copy.copy(auth_service.get_configuration(use_sandbox=False))
    streamer_config.proxy = None
    streamer = MarketDataStreamer(streamer_config)

    async def _handle_market_tick(data):
        """
        Callback for the streamer.
        'data' is the decoded protobuf to dict from the SDK.
        Handles ltpc, full, and option_greeks modes.
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
                # Handle three modes:
                # 1. ltpc: feed['ltpc']['ltp']
                # 2. full: feed['fullFeed']['marketFF']['ltpc']['ltp'] + optionGreeks
                # 3. option_greeks: feed['optionGreeks'][...] (Greeks only)

                # 1. Navigate to the feed level (handle nested structure)
                # option_greeks mode arrives via firstLevelWithGreeks in SDK V3.
                inner = (
                    feed.get("fullFeed")
                    or feed.get("ff")
                    or feed.get("firstLevelWithGreeks")
                    or feed.get("first_level_with_greeks")
                    or feed
                )
                if "marketFF" in inner:
                    inner = inner["marketFF"]
                elif "indexFF" in inner:
                    inner = inner["indexFF"]

                # 2. Extract LTP (present in ltpc and full modes; absent in option_greeks)
                ltpc = inner.get("ltpc", {})
                ltp = ltpc.get("ltp")

                # 3. Extract Greeks (present in full and option_greeks modes)
                # option_greeks mode: feed['optionGreeks'] directly
                # full mode: feed['fullFeed']['..']['optionGreeks'] or feed['optionGreeks']
                greeks = (
                    inner.get("optionGreeks")
                    or inner.get("option_greeks")
                    or feed.get("optionGreeks")
                    or feed.get("option_greeks")
                    or {}
                )
                delta = float(greeks.get("delta") or 0.0)
                theta = float(greeks.get("theta") or 0.0)
                iv = float(inner.get("iv") or greeks.get("iv") or 0.0)

                # 4. Extract volume (ltpc/full modes; absent in option_greeks)
                raw_volume = inner.get("vtt")
                volume = int(raw_volume) if raw_volume is not None else None

                # 5. Build message in IST timestamp
                ds = datetime.now(timezone(timedelta(hours=5, minutes=30))).timestamp()
                msg = [
                    "t",
                    instrument_key,
                    float(ltp) if ltp is not None else None,
                    int(volume) if volume is not None else None,
                    round(iv * 100, 2) if iv else 0.0,
                    round(delta, 4),
                    round(theta, 2),
                    int(ds)
                ]

                app.state.last_market_tick = {
                    "instrument_key": instrument_key,
                    "ltp": float(ltp) if ltp is not None else None,
                    "delta": round(delta, 4),
                    "theta": round(theta, 2),
                    "iv": round(iv * 100, 2),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                app.state.last_market_tick_epoch = time.monotonic()

                # Cache Greeks for option contracts (used by option chain endpoint)
                if delta or theta or iv:  # Only cache if Greeks are present
                    app.state.greeks_cache[instrument_key] = {
                        "delta": round(delta, 4),
                        "theta": round(theta, 2),
                        "iv": round(iv * 100, 2),
                    }

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

    # Start market data streamer with core indices; additional subscriptions added dynamically
    try:
        streamer.start(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"], mode="ltpc")
        app.state.market_streamer = streamer
        logger.info("📡 Market Data Streamer started with core indices (Nifty/Bank Nifty).")
    except Exception as e:
        logger.error(f"❌ Failed to start market streamer: {e}")

    # Start Portfolio Streamer
    # Portfolio streamers MUST use Live configuration for notifications
    portfolio_streamer_config = copy.copy(auth_service.get_configuration(use_sandbox=False))
    portfolio_streamer_config.proxy = None
    portfolio_streamer = PortfolioStreamer(portfolio_streamer_config)

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

    # --- Heartbeat & Periodic Status ---
    fallback_market_service = None
    try:
        fallback_market_service = MarketDataService(auth_service.get_configuration(use_sandbox=False))
        app.state.ltp_fallback_service = fallback_market_service
    except Exception as e:
        logger.warning(f"LTP fallback service unavailable: {e}")

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

                # If streamer ticks are stale, push LTP fallback ticks for subscribed symbols.
                stale_for = time.monotonic() - float(getattr(app.state, "last_market_tick_epoch", 0.0) or 0.0)
                if stale_for > 6.0 and fallback_market_service and instrument_subscriptions:
                    subscribed_keys = list(instrument_subscriptions.keys())
                    for key in subscribed_keys:
                        ltp = await asyncio.to_thread(fallback_market_service.get_ltp, key)
                        if ltp is None:
                            continue

                        msg = [
                            "t",
                            key,
                            float(ltp),
                            0,
                            0.0,
                            0.0,
                            0.0,
                            int(datetime.now(timezone(timedelta(hours=5, minutes=30))).timestamp()),
                        ]

                        app.state.last_market_tick = {
                            "instrument_key": key,
                            "ltp": float(ltp),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "source": "ltp_fallback",
                        }
                        app.state.last_market_tick_epoch = time.monotonic()

                        target_clients = instrument_subscriptions.get(key, set())
                        for client_ws in list(target_clients):
                            try:
                                await client_ws.send_json(msg)
                            except Exception:
                                pass
            except Exception:
                logger.debug("Periodic update loop encountered an error.", exc_info=True)
            await asyncio.sleep(10)

    _heartbeat_task = asyncio.create_task(_periodic_updates())
    app.state.heartbeat_task = _heartbeat_task

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    if hasattr(app.state, "heartbeat_task") and not app.state.heartbeat_task.done():
        app.state.heartbeat_task.cancel()
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
# Note: allow_credentials=True is incompatible with allow_origins=["*"] per CORS spec.
# For production, replace "*" with your actual frontend origin (e.g. "http://localhost:3000").
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
                    # NOTE: For option chains, prefer REST endpoint /market/option-chain instead.
                    # WebSocket subscriptions are best used for live-traded instruments (few contracts).
                    # Option chains contain many contracts; subscribing to all via WebSocket is inefficient.

                    keys_to_sub = [k.strip() for k in key.split(',') if k.strip()]

                    # Guard: Keep option subscriptions bounded while still covering an ATM window.
                    # option_greeks is lightweight, so a moderate cap is safe.
                    option_keys = [k for k in keys_to_sub if "NSE_FO" in k or "FO|" in k]
                    max_option_subs = 30
                    if len(option_keys) > max_option_subs:
                        logger.warning(
                            f"⚠️ Attempted to subscribe to {len(option_keys)} option contracts at once. "
                            f"Capping to {max_option_subs} option contracts for WS option_greeks mode."
                        )
                        # Keep all non-option subscriptions + first N option keys
                        non_option_keys = [k for k in keys_to_sub if k not in option_keys]
                        keys_to_sub = non_option_keys + option_keys[:max_option_subs]
                        option_keys = option_keys[:max_option_subs]

                    # Batch subscriptions by mode for efficiency
                    ltpc_batch = []
                    greeks_batch = []
                    full_batch = []

                    for k in keys_to_sub:
                        if k not in instrument_subscriptions:
                            instrument_subscriptions[k] = set()
                        instrument_subscriptions[k].add(ws)

                        if ws not in client_subscriptions:
                            client_subscriptions[ws] = set()
                        client_subscriptions[ws].add(k)

                        if len(instrument_subscriptions[k]) == 1:
                            # Smart mode selection based on instrument type
                            is_option = "NSE_FO" in k or "FO|" in k
                            if is_option:
                                # Use option_greeks mode: lightweight, provides only Greeks data
                                greeks_batch.append(k)
                            else:
                                # Indices/stocks: use ltpc (last traded price, lightweight)
                                ltpc_batch.append(k)

                    # Execute batch subscriptions
                    if hasattr(app.state, "market_streamer") and app.state.market_streamer:
                        try:
                            if ltpc_batch:
                                app.state.market_streamer.subscribe(ltpc_batch, mode="ltpc")
                                logger.info(f"📡 Batch subscribed ({len(ltpc_batch)} instruments, LTPC mode)")
                        except Exception as e:
                            logger.error(f"Failed to batch subscribe (ltpc): {e}")

                        try:
                            if greeks_batch:
                                app.state.market_streamer.subscribe(greeks_batch, mode="option_greeks")
                                logger.info(f"📡 Batch subscribed ({len(greeks_batch)} option contracts, OPTION_GREEKS mode)")
                        except Exception as e:
                            logger.error(f"Failed to batch subscribe (option_greeks): {e}")

                        try:
                            if full_batch:
                                app.state.market_streamer.subscribe(full_batch, mode="full")
                                logger.info(f"📡 Batch subscribed ({len(full_batch)} instruments, FULL mode)")
                        except Exception as e:
                            logger.error(f"Failed to batch subscribe (full): {e}")


                        # Immediate bootstrap tick for chart initialization.
                        ltp_service = getattr(app.state, "ltp_fallback_service", None)
                        if ltp_service:
                            try:
                                ltp = await asyncio.to_thread(ltp_service.get_ltp, k)
                                if ltp is not None:
                                    bootstrap_msg = [
                                        "t",
                                        k,
                                        float(ltp),
                                        0,
                                        0.0,
                                        0.0,
                                        0.0,
                                        int(datetime.now(timezone(timedelta(hours=5, minutes=30))).timestamp()),
                                    ]
                                    await ws.send_json(bootstrap_msg)
                            except Exception as e:
                                logger.debug(f"Bootstrap LTP send failed for {k}: {e}")

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
    token_valid, reason = auth.validate_token(use_sandbox=auth.settings.USE_SANDBOX)

    return {
        "status": "healthy",
        "auth": "valid" if token_valid else "invalid",
        "auth_reason": None if token_valid else reason,
        "api_version": get_settings().API_VERSION,
    }
