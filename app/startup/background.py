"""
Startup: Heartbeat and periodic background tasks.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


async def startup_background(app: FastAPI, broadcast_callback, instrument_subscriptions):
    """Launch the heartbeat / LTP-fallback background task."""

    async def _periodic_updates():
        while True:
            try:
                await broadcast_callback({
                    "type": "heartbeat",
                    "data": {"timestamp": datetime.now().isoformat()},
                })

                from app.engine import get_engine
                engine = get_engine()
                await broadcast_callback({"type": "status", "data": engine.get_status()})

                # If streamer ticks are stale, push LTP fallback ticks
                stale_for = time.monotonic() - float(
                    getattr(app.state, "last_market_tick_epoch", 0.0) or 0.0
                )
                fallback_service = getattr(app.state, "ltp_fallback_service", None)

                if stale_for > 6.0 and fallback_service and instrument_subscriptions:
                    subscribed_keys = list(instrument_subscriptions.keys())
                    for key in subscribed_keys:
                        ltp = await asyncio.to_thread(fallback_service.get_ltp, key)
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
                            int(datetime.now(IST).timestamp()),
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

    task = asyncio.create_task(_periodic_updates())
    app.state.heartbeat_task = task
