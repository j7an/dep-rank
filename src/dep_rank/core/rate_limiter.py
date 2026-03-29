"""Async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """Rate limiter using the token bucket algorithm.

    Args:
        rate: Maximum number of requests allowed per period.
        period: Time period in seconds.
    """

    def __init__(self, rate: int, period: float) -> None:
        self._rate = rate
        self._period = period
        self._tokens = float(rate)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) * (self._period / self._rate)
                await asyncio.sleep(wait_time)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._rate),
            self._tokens + elapsed * (self._rate / self._period),
        )
        self._last_refill = now
