"""
Startup: Market data and portfolio streamer wiring.

Contains TickMessage — a typed parser for Upstox protobuf-decoded dicts.
"""

import asyncio
import copy
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ── Typed tick parser ───────────────────────────────────────────


@dataclass(slots=True)
class TickMessage:
    """
    Parsed market tick from Upstox MarketDataStreamerV3.

    Handles three modes:
      - ltpc:           feed['ltpc']['ltp']
      - full:           feed['fullFeed']['marketFF'|'indexFF']['ltpc']['ltp'] + optionGreeks
      - option_greeks:  feed['firstLevelWithGreeks'|'optionGreeks'][...]
    """

    instrument_key: str
    ltp: float | None
    volume: int | None
    delta: float
    theta: float
    iv: float

    @classmethod
    def from_upstox(cls, instrument_key: str, feed: dict) -> "TickMessage":
        """
        Parse a single instrument's feed dict into a TickMessage.

        Raises ValueError if the feed structure is completely unrecognisable.
        """
        inner = (
            feed.get("fullFeed")
            or feed.get("ff")
            or feed.get("firstLevelWithGreeks")
            or feed.get("first_level_with_greeks")
            or feed
        )

        # Drill into marketFF / indexFF when present (full mode nesting)
        if isinstance(inner, dict):
            if "marketFF" in inner:
                inner = inner["marketFF"]
            elif "indexFF" in inner:
                inner = inner["indexFF"]

        # --- LTP (present in ltpc and full modes; absent in option_greeks) ---
        ltpc = inner.get("ltpc", {}) if isinstance(inner, dict) else {}
        ltp = ltpc.get("ltp")

        # --- Greeks (full and option_greeks modes) ---
        greeks = (
            inner.get("optionGreeks")
            or inner.get("option_greeks")
            or feed.get("optionGreeks")
            or feed.get("option_greeks")
            or {}
        ) if isinstance(inner, dict) else {}

        delta = float(greeks.get("delta") or 0.0)
        theta = float(greeks.get("theta") or 0.0)
        iv_raw = inner.get("iv") if isinstance(inner, dict) else None
        iv = float(iv_raw or greeks.get("iv") or 0.0)

        # --- Volume (ltpc/full modes; absent in option_greeks) ---
        raw_volume = inner.get("vtt") if isinstance(inner, dict) else None
        volume = int(raw_volume) if raw_volume is not None else None

        return cls(
            instrument_key=instrument_key,
            ltp=float(ltp) if ltp is not None else None,
            volume=volume,
            delta=round(delta, 4),
            theta=round(theta, 2),
            iv=round(iv * 100, 2) if iv else 0.0,
        )

    def to_ws_message(self) -> list:
        """Pack into the compact array format sent to frontend WebSocket clients."""
        ts = int(datetime.now(IST).timestamp())
        return [
            "t",
            self.instrument_key,
            self.ltp,
            self.volume,
            self.iv,
            self.delta,
            self.theta,
            ts,
        ]

    def to_state_dict(self) -> dict:
        """Build the dict stored in app.state.last_market_tick."""
        return {
            "instrument_key": self.instrument_key,
            "ltp": self.ltp,
            "delta": self.delta,
            "theta": self.theta,
            "iv": self.iv,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def has_greeks(self) -> bool:
        return bool(self.delta or self.theta or self.iv)


def _extract_feeds(data: dict) -> dict:
    """Extract the feeds dict from the top-level SDK message."""
    if not isinstance(data, dict):
        return {}
    feeds = data.get("feeds", {})
    if feeds:
        return feeds
    # Fallback: top-level keys look like instrument keys (e.g. "NSE_EQ|...")
    if any("|" in k for k in data.keys()):
        return data
    return {}


# ── Streamer setup ──────────────────────────────────────────────


async def startup_streams(
    app: FastAPI,
    ws_clients,
    instrument_subscriptions,
):
    """Configure and start market data + portfolio streamers."""
    from app.market_data.streamer import MarketDataStreamer, PortfolioStreamer
    from app.market_data.service import MarketDataService
    from app.auth.service import get_auth_service

    loop = asyncio.get_running_loop()
    auth_service = get_auth_service()

    app.state.last_market_tick = None
    app.state.last_market_tick_epoch = 0.0
    app.state.greeks_cache = {}

    # Market streamers MUST use Live configuration (Sandbox doesn't support market data)
    streamer_config = copy.copy(auth_service.get_configuration(use_sandbox=False))
    streamer_config.proxy = None
    streamer = MarketDataStreamer(streamer_config)

    async def _handle_market_tick(data):
        """Callback for streamer — parse via TickMessage and broadcast."""
        if not data:
            return

        feeds = _extract_feeds(data)
        if not feeds:
            return

        try:
            for instrument_key, feed in feeds.items():
                try:
                    tick = TickMessage.from_upstox(instrument_key, feed)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.debug(f"Skipping unparseable tick for {instrument_key}: {e}")
                    continue

                msg = tick.to_ws_message()

                app.state.last_market_tick = tick.to_state_dict()
                app.state.last_market_tick_epoch = time.monotonic()

                # Cache Greeks for option contracts (used by option chain endpoint)
                if tick.has_greeks:
                    app.state.greeks_cache[instrument_key] = {
                        "delta": tick.delta,
                        "theta": tick.theta,
                        "iv": tick.iv,
                    }

                target_clients = instrument_subscriptions.get(instrument_key, set())
                for client_ws in list(target_clients):
                    try:
                        await client_ws.send_json(msg)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error in _handle_market_tick: {e}")

    def sync_on_tick(message):
        asyncio.run_coroutine_threadsafe(_handle_market_tick(message), loop)

    streamer.on_tick = sync_on_tick

    try:
        streamer.start(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"], mode="ltpc")
        app.state.market_streamer = streamer
        logger.info("📡 Market Data Streamer started with core indices (Nifty/Bank Nifty).")
    except Exception as e:
        logger.error(f"❌ Failed to start market streamer: {e}")

    # --- Portfolio Streamer ---
    portfolio_streamer_config = copy.copy(auth_service.get_configuration(use_sandbox=False))
    portfolio_streamer_config.proxy = None
    portfolio_streamer = PortfolioStreamer(portfolio_streamer_config)

    async def _broadcast(msg):
        """Broadcast helper imported by background module too."""
        from app.main import broadcast_to_clients
        await broadcast_to_clients(msg)

    def sync_portfolio_update(message):
        asyncio.run_coroutine_threadsafe(
            _handle_portfolio_update(message),
            loop,
        )

    async def _handle_portfolio_update(message):
        try:
            from app.engine import get_engine
            eng = get_engine()
            await eng.handle_portfolio_update(message)
        except Exception as e:
            logger.error(f"Error processing portfolio update: {e}")
        await _broadcast({"type": "portfolio_update", "data": message})

    portfolio_streamer.on_update = sync_portfolio_update

    try:
        portfolio_streamer.start(
            order_update=True,
            position_update=True,
            holding_update=True,
            gtt_update=True,
        )
        app.state.portfolio_streamer = portfolio_streamer
        logger.info("📡 Portfolio Data Streamer started (with GTT updates).")
    except Exception as e:
        logger.error(f"❌ Failed to start portfolio streamer: {e}")

    # LTP fallback service
    try:
        fallback_market_service = MarketDataService(
            auth_service.get_configuration(use_sandbox=False)
        )
        app.state.ltp_fallback_service = fallback_market_service
    except Exception as e:
        logger.warning(f"LTP fallback service unavailable: {e}")
