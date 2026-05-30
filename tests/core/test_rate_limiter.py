"""Tests for async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from dep_rank.core.rate_limiter import TokenBucketRateLimiter


class TestTokenBucketRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_within_limit(self) -> None:
        limiter = TokenBucketRateLimiter(rate=10, period=1.0)
        for _ in range(10):
            await limiter.acquire()

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self) -> None:
        limiter = TokenBucketRateLimiter(rate=2, period=1.0)
        await limiter.acquire()
        await limiter.acquire()
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4

    @pytest.mark.asyncio
    async def test_tokens_replenish(self) -> None:
        limiter = TokenBucketRateLimiter(rate=2, period=0.5)
        await limiter.acquire()
        await limiter.acquire()
        await asyncio.sleep(0.6)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_try_acquire_consumes_when_available(self) -> None:
        limiter = TokenBucketRateLimiter(rate=2, period=60.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True

    def test_try_acquire_returns_false_when_empty(self) -> None:
        limiter = TokenBucketRateLimiter(rate=1, period=60.0)
        assert limiter.try_acquire() is True
        # bucket now empty; over a 60s period it will not refill within the test
        assert limiter.try_acquire() is False

    @pytest.mark.asyncio
    async def test_try_acquire_refuses_while_lock_held(self) -> None:
        """A foreground caller waiting in acquire() holds the lock across its sleep;
        try_acquire must yield to it rather than steal the token it is about to claim."""
        # rate=5 leaves tokens available, so the only thing that can refuse
        # try_acquire is the held lock — simulating the window where a foreground
        # caller is sleeping in acquire() and a lockless decrement would steal the
        # token it is about to claim.
        limiter = TokenBucketRateLimiter(rate=5, period=60.0)
        async with limiter._lock:  # simulate a foreground caller holding the lock
            assert limiter.try_acquire() is False
        # once the lock is free, background may proceed
        assert limiter.try_acquire() is True

    def test_tokens_available_reports_remaining(self) -> None:
        limiter = TokenBucketRateLimiter(rate=3, period=60.0)
        assert limiter.tokens_available() == pytest.approx(3.0, abs=0.05)
        limiter.try_acquire()
        assert limiter.tokens_available() == pytest.approx(2.0, abs=0.05)

    def test_try_acquire_reserve_leaves_headroom(self) -> None:
        """``reserve`` makes a background caller leave tokens for the foreground."""
        limiter = TokenBucketRateLimiter(rate=2, period=60.0)
        # 2 tokens; reserve=2 requires >=3 to consume -> refuse, take nothing.
        assert limiter.try_acquire(reserve=2) is False
        assert limiter.tokens_available() == pytest.approx(2.0, abs=0.05)
        # reserve=1 requires >=2 -> consume one, leaving ~1 for the foreground.
        assert limiter.try_acquire(reserve=1) is True
        assert limiter.tokens_available() == pytest.approx(1.0, abs=0.05)
