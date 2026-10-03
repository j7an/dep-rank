"""Async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable

# Rate budgets (requests per RATE_PERIOD seconds).
AUTH_RATE = 60
UNAUTH_RATE = 1
RATE_PERIOD = 60.0
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 120
BACKGROUND_PAUSE_SECONDS = 60.0


def backoff_delay(attempt: int) -> float:
    """Return full-jitter exponential backoff capped at the retry maximum."""
    return random.uniform(0, min(RETRY_BASE_SECONDS * 2**attempt, RETRY_MAX_SECONDS))  # noqa: S311


class RateLimiter:
    """Token bucket plus shared 429 state (backoff growth and background pause)."""

    def __init__(
        self, rate: int, period: float, *, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = rate
        self._period = period
        self._tokens = float(rate)
        self._now = now
        self._last_refill = self._now()
        self._lock = asyncio.Lock()
        self._last_429 = float("-inf")
        self._backoff_attempt = 0

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
        same synchronous call (rather than a separate availability read) means no
        other coroutine can run between them, avoiding a check-then-consume race.

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

    def _refill(self) -> None:
        now = self._now()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._rate),
            self._tokens + elapsed * (self._rate / self._period),
        )
        self._last_refill = now

    def note_429(self, retry_after: float | None = None) -> float:
        """Record a 429 and return the shared backoff delay in seconds."""
        self._last_429 = self._now()
        delay = backoff_delay(self._backoff_attempt)
        self._backoff_attempt += 1
        return max(delay, retry_after) if retry_after is not None else delay

    def note_success(self) -> None:
        """Reset backoff growth after a successful response."""
        self._backoff_attempt = 0

    def background_paused(self) -> bool:
        """Whether background work must yield after a recent 429."""
        return self._now() - self._last_429 < BACKGROUND_PAUSE_SECONDS
