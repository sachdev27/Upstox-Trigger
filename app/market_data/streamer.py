"""
WebSocket Market Data Streamer — real-time price feeds.

Refactored from legacy/live_data/websocket_market.py.
Uses the Upstox SDK's WebSocket API with protobuf decoding.
"""

import asyncio
import json
import logging
import ssl
from typing import Callable, Any

import upstox_client
import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)

# Try importing protobuf — will be generated/available at runtime
try:
    from legacy.live_data import marketDataFeed_pb2 as pb

    HAS_PROTOBUF = True
except ImportError:
    HAS_PROTOBUF = False
    logger.warning("Protobuf module not found — streamer will use JSON mode.")


class MarketDataStreamer:
    """
    Async WebSocket streamer for real-time market data.

    Usage:
        streamer = MarketDataStreamer(configuration)
        streamer.on_tick = my_callback
        await streamer.connect(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"])
    """

    def __init__(self, configuration: upstox_client.Configuration):
        self.config = configuration
        self.settings = get_settings()
        self.on_tick: Callable[[dict], None] | None = None
        self._websocket = None
        self._running = False

    async def connect(
        self,
        instrument_keys: list[str],
        mode: str = "full",
    ):
        """
        Connect to Upstox WebSocket and start streaming.

        Args:
            instrument_keys: List of instrument keys to subscribe
            mode: "full", "ltpc", or "option_greeks"
        """
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Get WebSocket authorization
        api = upstox_client.WebsocketApi(
            upstox_client.ApiClient(self.config)
        )
        auth_response = api.get_market_data_feed_authorize(
            self.settings.API_VERSION
        )
        ws_url = auth_response.data.authorized_redirect_uri

        logger.info(f"Connecting to WebSocket: {ws_url[:50]}...")

        async with websockets.connect(ws_url, ssl=ssl_context) as ws:
            self._websocket = ws
            self._running = True
            logger.info("WebSocket connected.")

            await asyncio.sleep(1)

            # Subscribe to instruments
            subscribe_msg = {
                "guid": "upstox-automation",
                "method": "sub",
                "data": {
                    "mode": mode,
                    "instrumentKeys": instrument_keys,
                },
            }
            await ws.send(json.dumps(subscribe_msg).encode("utf-8"))
            logger.info(
                f"Subscribed to {len(instrument_keys)} instruments in '{mode}' mode."
            )

            # Receive loop
            while self._running:
                try:
                    message = await ws.recv()
                    data = self._decode(message)
                    if data and self.on_tick:
                        self.on_tick(data)
                except websockets.ConnectionClosed:
                    logger.warning("WebSocket connection closed.")
                    break
                except Exception as e:
                    logger.error(f"Streamer error: {e}")

    async def disconnect(self):
        """Stop the streamer."""
        self._running = False
        if self._websocket:
            await self._websocket.close()
            logger.info("WebSocket disconnected.")

    def _decode(self, buffer: bytes) -> dict | None:
        """Decode protobuf or JSON WebSocket message."""
        if HAS_PROTOBUF:
            try:
                from google.protobuf.json_format import MessageToDict

                feed = pb.FeedResponse()
                feed.ParseFromString(buffer)
                return MessageToDict(feed)
            except Exception:
                pass

        # Fallback to JSON
        try:
            return json.loads(buffer)
        except Exception:
            return None
