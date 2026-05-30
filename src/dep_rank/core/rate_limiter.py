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

    def try_acquire(self, reserve: float = 0.0) -> bool:
        """Consume one token if at least ``1 + reserve`` are available now; never blocks.

        Returns True and consumes one token when enough are available, else returns
        False and consumes nothing. Used by background work that must yield to
        foreground callers.

        ``reserve`` lets a background caller leave headroom for the foreground: with
        ``reserve=1`` a token is taken only when >=2 remain, so a foreground caller
        still finds one waiting. Keeping the headroom check and the decrement in the
        same synchronous call (rather than a separate ``tokens_available()`` read
        elsewhere) means no other coroutine can run between them, avoiding a
        check-then-consume race.

        Refuses (returns False) when the bucket lock is held: a foreground caller
        waiting in ``acquire()`` holds the lock across its ``await sleep`` for a
        token, so a lockless decrement here could steal the token it is about to
        claim. Checking ``self._lock.locked()`` makes background work yield instead.
        """
        if self._lock.locked():
            return False
        self._refill()
        if self._tokens >= 1.0 + reserve:
            self._tokens -= 1.0
            return True
        return False

    def tokens_available(self) -> float:
        """Return the number of whole/partial tokens currently in the bucket."""
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._rate),
            self._tokens + elapsed * (self._rate / self._period),
        )
        self._last_refill = now
