"""Tests for async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from dep_rank.core.rate_limiter import (
    BACKGROUND_PAUSE_SECONDS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    AdaptiveRateLimiter,
    RateLimiter,
    TokenBucketRateLimiter,
    backoff_delay,
)


class TestTokenBucketRateLimiter:
    async def test_allows_within_limit(self) -> None:
        limiter = TokenBucketRateLimiter(rate=10, period=1.0)
        for _ in range(10):
            await limiter.acquire()

    async def test_blocks_over_limit(self) -> None:
        limiter = TokenBucketRateLimiter(rate=2, period=1.0)
        await limiter.acquire()
        await limiter.acquire()
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4

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


class TestAdaptiveRateLimiter:
    def test_for_token_unauthenticated_is_one_per_minute(self) -> None:
        limiter = AdaptiveRateLimiter.for_token(None, concurrency=3)
        # One token available immediately, none after (60s period won't refill in-test).
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_for_token_authenticated_is_sixty_per_minute(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3)
        # 60 tokens in the bucket: 60 immediate acquisitions succeed.
        assert all(limiter.try_acquire() for _ in range(60))
        assert limiter.try_acquire() is False

    def test_note_429_halves_concurrency_floor_one(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=8, jitter=False)
        assert limiter.current_max_concurrency == 8
        limiter.note_429()
        assert limiter.current_max_concurrency == 4
        limiter.note_429()
        assert limiter.current_max_concurrency == 2
        limiter.note_429()
        assert limiter.current_max_concurrency == 1
        limiter.note_429()
        assert limiter.current_max_concurrency == 1  # floor

    def test_note_429_backoff_grows(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3, jitter=False)
        d0 = limiter.note_429()
        d1 = limiter.note_429()
        assert d1 > d0  # exponential growth without jitter

    def test_note_429_honors_retry_after(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3, jitter=False)
        delay = limiter.note_429(retry_after=99.0)
        assert delay >= 99.0

    def test_note_429_backoff_caps_at_max(self) -> None:
        """Exponential growth is clamped at RETRY_MAX_SECONDS."""
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3, jitter=False)
        for _ in range(5):  # 5, 10, 20, 40, 80 — the next would overshoot the cap
            limiter.note_429()
        delay = limiter.note_429()  # 5 * 2**5 = 160, clamped to 120
        assert delay == RETRY_MAX_SECONDS

    def test_note_429_jitter_returns_in_range(self) -> None:
        """With jitter, the first delay falls in [0, exp] where exp = base (attempt 0)."""
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3, jitter=True)
        delay = limiter.note_429()
        assert 0.0 <= delay <= RETRY_BASE_SECONDS

    def test_note_success_recovers_after_recovery_period(self) -> None:
        clock = {"t": 1000.0}
        limiter = AdaptiveRateLimiter.for_token(
            "ghp_x", concurrency=4, jitter=False, now=lambda: clock["t"]
        )
        limiter.note_429()  # -> 2
        assert limiter.current_max_concurrency == 2
        clock["t"] += 5.0
        limiter.note_success()  # too soon; no increase
        assert limiter.current_max_concurrency == 2
        clock["t"] += 60.0
        limiter.note_success()  # past recovery_period; +1
        assert limiter.current_max_concurrency == 3

    def test_429_restarts_recovery_clock(self) -> None:
        """A 429 resets the recovery baseline: a clean response immediately after it
        must NOT recover, even on a limiter that has lived past RECOVERY_PERIOD."""
        clock = {"t": 1000.0}
        limiter = AdaptiveRateLimiter.for_token(
            "ghp_x", concurrency=4, jitter=False, now=lambda: clock["t"]
        )
        clock["t"] += 100.0  # idle well past RECOVERY_PERIOD before any 429
        limiter.note_429()  # 4 -> 2, and restart the recovery clock at t=1100
        assert limiter.current_max_concurrency == 2
        limiter.note_success()  # immediate clean response; must NOT recover
        assert limiter.current_max_concurrency == 2
        clock["t"] += 60.0  # now a full recovery period has elapsed since the 429
        limiter.note_success()
        assert limiter.current_max_concurrency == 3

    def test_tokens_available_delegates_to_bucket(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3)
        assert limiter.tokens_available() == pytest.approx(60.0, abs=0.05)
        limiter.try_acquire()
        assert limiter.tokens_available() == pytest.approx(59.0, abs=0.05)

    async def test_acquire_delegates_to_bucket(self) -> None:
        limiter = AdaptiveRateLimiter.for_token("ghp_x", concurrency=3)
        await limiter.acquire()  # does not raise


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
