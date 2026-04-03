"""
Shared Upstox API rate limiter — prevents exceeding upstream quotas.

Enforces Upstox Historical API Limits:
- 50 requests per second  (using 45 for safety)
- 500 requests per minute (using 450 for safety)
- 2000 requests per 30 minutes (using 1900 for safety)

Used by both the engine (strategy evaluations) and market data routes.
"""

import asyncio
import time
from collections import deque


class UpstoxRateLimiter:

    def __init__(self):
        self.lock = asyncio.Lock()
        self.history_sec = deque()
        self.history_min = deque()
        self.history_30min = deque()

    async def wait_for_token(self):
        async with self.lock:
            while True:
                now = time.monotonic()

                # Cleanup old requests
                while self.history_sec and now - self.history_sec[0] > 1.0:
                    self.history_sec.popleft()
                while self.history_min and now - self.history_min[0] > 60.0:
                    self.history_min.popleft()
                while self.history_30min and now - self.history_30min[0] > 1800.0:
                    self.history_30min.popleft()

                # Check limits
                if len(self.history_sec) >= 45:
                    await asyncio.sleep(1.0 - (now - self.history_sec[0]) + 0.01)
                    continue
                if len(self.history_min) >= 450:
                    await asyncio.sleep(60.0 - (now - self.history_min[0]) + 0.1)
                    continue
                if len(self.history_30min) >= 1900:
                    await asyncio.sleep(1800.0 - (now - self.history_30min[0]) + 1.0)
                    continue

                # Consume token
                self.history_sec.append(now)
                self.history_min.append(now)
                self.history_30min.append(now)
                break

    @property
    def usage(self) -> dict:
        """Current usage snapshot (for monitoring endpoints)."""
        now = time.monotonic()
        return {
            "per_sec": sum(1 for t in self.history_sec if now - t <= 1.0),
            "per_min": sum(1 for t in self.history_min if now - t <= 60.0),
            "per_30min": sum(1 for t in self.history_30min if now - t <= 1800.0),
        }


# Module-level singleton
_limiter: UpstoxRateLimiter | None = None


def get_rate_limiter() -> UpstoxRateLimiter:
    """Get or create the shared rate limiter singleton."""
    global _limiter
    if _limiter is None:
        _limiter = UpstoxRateLimiter()
    return _limiter
