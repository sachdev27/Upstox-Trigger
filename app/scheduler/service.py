"""
Scheduler Service — manages pre-market, market-hours, and post-market tasks.

Runs on a background thread, executing tasks at the right time during
Indian market hours (9:15 AM – 3:30 PM IST).
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class SchedulerService:
    """
    Manages scheduled tasks aligned with Indian market hours.

    Hooks:
        - pre_market (8:45 AM IST)  → Token validation, instrument download
        - market_open (9:15 AM IST) → Start WebSocket feeds, activate strategies
        - candle_check             → Run every N minutes during market hours
        - market_close (3:30 PM IST)→ Square-off intraday, stop feeds
        - post_market (3:45 PM IST) → Daily report, archive logs
    """

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._callbacks: dict[str, list[Callable]] = {
            "pre_market": [],
            "market_open": [],
            "candle_check": [],
            "market_close": [],
            "post_market": [],
        }

    def on(self, event: str, callback: Callable):
        """Register a callback for a scheduler event."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)
        else:
            raise ValueError(f"Unknown event: {event}. Use: {list(self._callbacks.keys())}")

    def start(self):
        """Start the scheduler with all registered tasks."""

        # Pre-market: 8:45 AM IST, Mon-Fri
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(hour=8, minute=45, day_of_week="mon-fri"),
            args=["pre_market"],
            id="pre_market",
            name="Pre-Market Tasks",
        )

        # Market open: 9:15 AM IST, Mon-Fri
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(hour=9, minute=15, day_of_week="mon-fri"),
            args=["market_open"],
            id="market_open",
            name="Market Open Tasks",
        )

        # Candle check: market hours 9:16 AM – 3:29 PM IST, Mon–Fri
        # Run every 5 seconds for faster, clock-aligned evaluation in AUTO mode.
        # Job A: 9:16–9:59
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(
                second="*/5", minute="16-59", hour=9, day_of_week="mon-fri"
            ),
            args=["candle_check"],
            id="candle_check_9",
            name="Candle Check 5s (9:16-9:59)",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=30,
        )

        # Job B: 10:00–14:59
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(
                second="*/5", minute="*", hour="10-14", day_of_week="mon-fri"
            ),
            args=["candle_check"],
            id="candle_check_main",
            name="Candle Check 5s (10:00-14:59)",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=30,
        )

        # Job C: 15:00–15:29
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(
                second="*/5", minute="0-29", hour=15, day_of_week="mon-fri"
            ),
            args=["candle_check"],
            id="candle_check_afternoon",
            name="Candle Check 5s (15:00-15:29)",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=30,
        )

        # Market close: 3:30 PM IST, Mon-Fri
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
            args=["market_close"],
            id="market_close",
            name="Market Close Tasks",
        )

        # Post-market: 3:45 PM IST, Mon-Fri
        self.scheduler.add_job(
            self._run_callbacks,
            CronTrigger(hour=15, minute=45, day_of_week="mon-fri"),
            args=["post_market"],
            id="post_market",
            name="Post-Market Tasks",
        )

        self.scheduler.start()
        logger.info("Scheduler started with market-hours task schedule.")

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

    async def _run_callbacks(self, event: str):
        """Execute all callbacks for an event."""
        callbacks = self._callbacks.get(event, [])
        logger.info(f"[{event}] Running {len(callbacks)} callback(s)...")

        for cb in callbacks:
            try:
                result = cb()
                # Support async callbacks
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error(f"[{event}] Callback error: {e}")

    def is_market_hours(self) -> bool:
        """Check if we're currently in Indian market hours."""
        now = datetime.now(IST)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close and now.weekday() < 5
