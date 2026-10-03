"""Tests for async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from dep_rank.core.rate_limiter import (
    BACKGROUND_PAUSE_SECONDS,
    RateLimiter,
    backoff_delay,
)


class TestRateLimiterBucket:
    async def test_allows_within_limit(self) -> None:
        limiter = RateLimiter(rate=10, period=1.0)
        for _ in range(10):
            await limiter.acquire()

    async def test_blocks_over_limit(self) -> None:
        limiter = RateLimiter(rate=2, period=1.0)
        await limiter.acquire()
        await limiter.acquire()
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4

    async def test_tokens_replenish(self) -> None:
        limiter = RateLimiter(rate=2, period=0.5)
        await limiter.acquire()
        await limiter.acquire()
        await asyncio.sleep(0.6)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_try_acquire_consumes_when_available(self) -> None:
        limiter = RateLimiter(rate=2, period=60.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True

    def test_try_acquire_returns_false_when_empty(self) -> None:
        limiter = RateLimiter(rate=1, period=60.0)
        assert limiter.try_acquire() is True
        # bucket now empty; over a 60s period it will not refill within the test
        assert limiter.try_acquire() is False

    async def test_try_acquire_refuses_while_lock_held(self) -> None:
        """A foreground caller waiting in acquire() holds the lock across its sleep;
        try_acquire must yield to it rather than steal the token it is about to claim."""
        # rate=5 leaves tokens available, so the only thing that can refuse
        # try_acquire is the held lock — simulating the window where a foreground
        # caller is sleeping in acquire() and a lockless decrement would steal the
        # token it is about to claim.
        limiter = RateLimiter(rate=5, period=60.0)
        async with limiter._lock:  # simulate a foreground caller holding the lock
            assert limiter.try_acquire() is False
        # once the lock is free, background may proceed
        assert limiter.try_acquire() is True

    def test_try_acquire_reserve_leaves_headroom(self) -> None:
        """``reserve`` makes a background caller leave tokens for the foreground."""
        limiter = RateLimiter(rate=2, period=60.0)
        # 2 tokens; reserve=2 requires >=3 to consume -> refuse, take nothing.
        assert limiter.try_acquire(reserve=2) is False
        # reserve=1 requires >=2 -> consume one, leaving ~1 for the foreground.
        assert limiter.try_acquire(reserve=1) is True
        assert limiter.try_acquire(reserve=1) is False
        assert limiter.try_acquire() is True


class TestRateLimiter:
    def test_background_paused_for_60s_after_429(self) -> None:
        clock = {"t": 1000.0}
        limiter = RateLimiter(60, 60.0, now=lambda: clock["t"])
        assert limiter.background_paused() is False
        limiter.note_429()
        clock["t"] += BACKGROUND_PAUSE_SECONDS - 1
        assert limiter.background_paused() is True
        clock["t"] += 1
        assert limiter.background_paused() is False

    def test_backoff_grows_and_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "uniform", lambda a, b: b)
        limiter = RateLimiter(60, 60.0)
        assert [limiter.note_429() for _ in range(6)] == [5, 10, 20, 40, 80, 120]

    def test_retry_after_overrides_shorter_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "uniform", lambda a, b: b)
        assert RateLimiter(60, 60.0).note_429(retry_after=99.0) == 99.0

    def test_note_success_resets_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "uniform", lambda a, b: b)
        limiter = RateLimiter(60, 60.0)
        limiter.note_429()
        limiter.note_429()
        limiter.note_success()
        assert limiter.note_429() == 5

    def test_backoff_delay_is_full_jitter_within_cap(self) -> None:
        for attempt in range(8):
            assert 0.0 <= backoff_delay(attempt) <= min(5 * 2**attempt, 120)

    def test_try_acquire_refills_using_injected_clock(self) -> None:
        clock = {"t": 1000.0}
        limiter = RateLimiter(2, 60.0, now=lambda: clock["t"])
        assert limiter.try_acquire(reserve=2) is False
        assert limiter.try_acquire(reserve=1) is True
        assert limiter.try_acquire(reserve=1) is False
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        clock["t"] += 15.0
        assert limiter.try_acquire() is False
        clock["t"] += 15.0
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        clock["t"] += 300.0
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    async def test_acquire_waits_and_background_yields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"t": 1000.0}
        limiter = RateLimiter(1, 60.0, now=lambda: clock["t"])
        await limiter.acquire()
        assert limiter.try_acquire() is False
        waits: list[float] = []

        async def sleep(delay: float) -> None:
            waits.append(delay)
            clock["t"] += delay
            assert limiter.try_acquire() is False

        monkeypatch.setattr(asyncio, "sleep", sleep)
        await limiter.acquire()
        assert waits == [60.0]
        assert limiter.try_acquire() is False
        clock["t"] += 60.0
        assert limiter.try_acquire() is True

    def test_success_does_not_clear_background_pause(self) -> None:
        clock = {"t": 1000.0}
        limiter = RateLimiter(60, 60.0, now=lambda: clock["t"])
        limiter.note_429()
        clock["t"] += 30.0
        limiter.note_success()
        assert limiter.background_paused() is True
        limiter.note_429()
        clock["t"] += 59.0
        assert limiter.background_paused() is True
        clock["t"] += 1.0
        assert limiter.background_paused() is False
