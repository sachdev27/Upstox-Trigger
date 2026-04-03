"""
Startup: Engine initialization and scheduler wiring.
"""

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


async def startup_engine(app: FastAPI, broadcast_callback):
    """Initialize the trading engine and wire the scheduler."""
    from app.engine import get_engine
    from app.scheduler.service import SchedulerService

    engine = get_engine()
    engine.initialize()
    engine.broadcast_callback = broadcast_callback

    scheduler = SchedulerService()

    async def _scheduled_run_cycle():
        """Called automatically every 5 seconds during market hours."""
        if engine.auto_mode:
            logger.info("🤖 [AUTO MODE] Running scheduled cycle...")
            await engine.run_cycle()
            await broadcast_callback({"type": "status", "data": engine.get_status()})

    scheduler.on("candle_check", _scheduled_run_cycle)

    async def _scheduled_square_off():
        """Called at market close (3:30 PM IST) to force-exit all open positions."""
        logger.info("🏁 [MARKET CLOSE] Squaring off all open positions...")
        await engine.square_off_all()
        await broadcast_callback({"type": "status", "data": engine.get_status()})

    scheduler.on("market_close", _scheduled_square_off)
    scheduler.start()
    app.state.scheduler = scheduler
