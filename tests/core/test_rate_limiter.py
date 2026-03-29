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
