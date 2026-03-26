"""
Market Data Streamer — uses the SDK's built-in MarketDataStreamerV3.

This replaces our entire custom WebSocket implementation with the SDK's
battle-tested streamer that handles protobuf decoding, auto-reconnect,
and subscription management natively.

SDK docs: https://upstox.com/developer/api-documentation/streamer-function
"""

import logging
from typing import Callable

import upstox_client

from app.config import get_settings

logger = logging.getLogger(__name__)


class MarketDataStreamer:
    """
    Thin wrapper around the SDK's MarketDataStreamerV3.

    Usage:
        streamer = MarketDataStreamer(configuration)
        streamer.on_tick = my_callback
        streamer.start(["NSE_INDEX|Nifty 50"])
    """

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self.settings = get_settings()
        self._streamer: upstox_client.MarketDataStreamerV3 | None = None
        self.on_tick: Callable[[dict], None] | None = None
        self.on_open: Callable[[], None] | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_error: Callable[[Exception], None] | None = None

    def start(
        self,
        instrument_keys: list[str],
        mode: str = "full",
    ):
        """
        Start streaming market data for the given instruments.

        Args:
            instrument_keys: e.g. ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]
            mode: "full", "ltpc", "full_d30", or "option_greeks"
        """
        api_client = upstox_client.ApiClient(self.config)
        self._streamer = upstox_client.MarketDataStreamerV3(
            api_client, instrument_keys, mode
        )

        # Enable auto-reconnect (retry 5 times, 10 second interval)
        self._streamer.auto_reconnect(enable=True, interval=10, retry_count=5)

        # Wire up event handlers
        self._streamer.on("open", self._handle_open)
        self._streamer.on("message", self._handle_message)
        self._streamer.on("close", self._handle_close)
        self._streamer.on("error", self._handle_error)
        self._streamer.on("reconnecting", self._handle_reconnecting)

        logger.info(f"Starting market data stream for {len(instrument_keys)} instruments ({mode})...")
        self._streamer.connect()

    def subscribe(self, instrument_keys: list[str], mode: str = "full"):
        """Subscribe to additional instruments on a running connection."""
        if self._streamer:
            self._streamer.subscribe(instrument_keys, mode)
            logger.info(f"Subscribed to {len(instrument_keys)} more instruments.")

    def unsubscribe(self, instrument_keys: list[str]):
        """Unsubscribe from instruments."""
        if self._streamer:
            self._streamer.unsubscribe(instrument_keys)
            logger.info(f"Unsubscribed from {len(instrument_keys)} instruments.")

    def change_mode(self, instrument_keys: list[str], mode: str):
        """Change the data mode for subscribed instruments."""
        if self._streamer:
            self._streamer.change_mode(instrument_keys, mode)

    def stop(self):
        """Disconnect the streamer."""
        if self._streamer:
            self._streamer.disconnect()
            logger.info("Market data streamer disconnected.")

    # ── Event handlers ──────────────────────────────────────────

    def _handle_open(self, *args):
        logger.info("✅ Market data WebSocket connected.")
        if self.on_open:
            self.on_open()

    def _handle_message(self, message):
        if self.on_tick:
            self.on_tick(message)

    def _handle_close(self, *args):
        logger.info("🔌 Market data WebSocket closed.")
        if self.on_close:
            self.on_close()

    def _handle_error(self, error):
        logger.error(f"❌ Market data streamer error: {error}")
        if self.on_error:
            self.on_error(error)

    def _handle_reconnecting(self, *args):
        logger.info("🔄 Reconnecting to market data stream...")


class PortfolioStreamer:
    """
    Thin wrapper around the SDK's PortfolioDataStreamer.
    Receives real-time order and position updates.
    """

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self._streamer: upstox_client.PortfolioDataStreamer | None = None
        self.on_update: Callable[[dict], None] | None = None

    def start(
        self,
        order_update: bool = True,
        position_update: bool = True,
        holding_update: bool = True,
        gtt_update: bool = False
    ):
        """Start receiving portfolio updates."""
        api_client = upstox_client.ApiClient(self.config)
        self._streamer = upstox_client.PortfolioDataStreamer(
            api_client,
            order_update=order_update,
            position_update=position_update,
            holding_update=holding_update,
            gtt_update=gtt_update
        )
        self._streamer.on("message", self._handle_message)
        
        # Add event handlers for connectivity status
        self._streamer.on("open", self._handle_open)
        self._streamer.on("close", self._handle_close)
        self._streamer.on("error", self._handle_error)
        
        logger.info(
            f"Starting portfolio data stream (Orders: {order_update}, "
            f"Positions: {position_update}, Holdings: {holding_update})..."
        )
        self._streamer.connect()

    def _handle_open(self, *args):
        logger.info("✅ Portfolio WebSocket connected.")

    def _handle_close(self, *args):
        logger.info("🔌 Portfolio WebSocket closed.")

    def _handle_error(self, error):
        logger.error(f"❌ Portfolio streamer error: {error}")

    def _handle_message(self, message):
        if self.on_update:
            self.on_update(message)

    def stop(self):
        """Stop portfolio stream."""
        if self._streamer:
            self._streamer.disconnect()
