"""Async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable


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


# Rate budgets (requests per RATE_PERIOD seconds).
AUTH_RATE = 60
UNAUTH_RATE = 1
RATE_PERIOD = 60.0
RECOVERY_PERIOD = 60.0
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 120


class AdaptiveRateLimiter:
    """Token-bucket limiter with AIMD concurrency control and 429 backoff.

    ``current_max_concurrency`` is an *advisory* AIMD bound. It is **not** consulted
    by the serial foreground walk — that uses a fixed ``Semaphore(concurrency)``, and
    pagination is intrinsically serial (page N's cursor needs page N-1's HTML). It
    instead governs the 429 backoff sleep and the Phase 3 SWR background-refresh gate
    (refresh suppressed at the floor of 1). Per-scrape by default; inject a shared
    instance for process-wide budgeting across concurrent scrapes.
    """

    def __init__(
        self,
        rate: int,
        period: float,
        concurrency: int,
        *,
        jitter: bool = True,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bucket = TokenBucketRateLimiter(rate, period)
        self._ceiling = concurrency
        self.current_max_concurrency = concurrency
        self._backoff_attempt = 0
        self._jitter = jitter
        self._now = now
        self._last_increase = now()

    @classmethod
    def for_token(
        cls,
        token: str | None,
        concurrency: int = 3,
        *,
        jitter: bool = True,
        now: Callable[[], float] = time.monotonic,
    ) -> AdaptiveRateLimiter:
        """Build a limiter sized for the auth mode: 60/min with a token, 1/min without."""
        rate = AUTH_RATE if token else UNAUTH_RATE
        return cls(rate, RATE_PERIOD, concurrency, jitter=jitter, now=now)

    async def acquire(self) -> None:
        """Blocking acquire for foreground fetches."""
        await self._bucket.acquire()

    def try_acquire(self, reserve: float = 0.0) -> bool:
        """Non-blocking acquire for background (SWR) refreshes; see §3.

        ``reserve`` is forwarded to the bucket so a background refresh leaves
        headroom for foreground fetches (see ``TokenBucketRateLimiter.try_acquire``).
        """
        return self._bucket.try_acquire(reserve)

    def tokens_available(self) -> float:
        return self._bucket.tokens_available()

    def note_429(self, retry_after: float | None = None) -> float:
        """Record a 429: halve advisory concurrency, restart the recovery clock, and
        return the seconds to sleep."""
        self.current_max_concurrency = max(1, self.current_max_concurrency // 2)
        # A 429 must restart the additive-recovery timer: otherwise, on a limiter that
        # has lived longer than RECOVERY_PERIOD, the very next clean response would step
        # concurrency back up immediately, defeating the multiplicative decrease (and
        # the SWR `current_max_concurrency <= 1` suppression gate in §3).
        self._last_increase = self._now()
        exp = min(RETRY_BASE_SECONDS * (2**self._backoff_attempt), RETRY_MAX_SECONDS)
        self._backoff_attempt += 1
        delay = random.uniform(0, exp) if self._jitter else float(exp)  # noqa: S311
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    def note_success(self) -> None:
        """Record a clean response: reset backoff; additively recover concurrency."""
        self._backoff_attempt = 0
        if (
            self.current_max_concurrency < self._ceiling
            and self._now() - self._last_increase >= RECOVERY_PERIOD
        ):
            self.current_max_concurrency += 1
            self._last_increase = self._now()
