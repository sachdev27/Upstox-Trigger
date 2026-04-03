"""
Upstox Trading Automation — FastAPI Application.

Entry point: uvicorn app.main:app --reload --port 8000
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.config import get_settings, BASE_DIR
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
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))

    # Configure global proxy behavior early so all SDK/API clients inherit it.
    from app.network_proxy import configure_network_proxies
    configure_network_proxies(settings)

    logger.info("🚀 Starting Upstox Trading Automation...")

    # ── Startup pipeline (each module is focused & testable) ──
    from app.startup.database import startup_database
    from app.startup.engine import startup_engine
    from app.startup.streams import startup_streams
    from app.startup.background import startup_background

    await startup_database()
    await startup_engine(app, broadcast_to_clients)
    await startup_streams(app, ws_clients, instrument_subscriptions)
    await startup_background(app, broadcast_to_clients, instrument_subscriptions)

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    if hasattr(app.state, "heartbeat_task") and not app.state.heartbeat_task.done():
        app.state.heartbeat_task.cancel()
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.stop()
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
