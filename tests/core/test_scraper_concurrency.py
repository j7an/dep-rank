"""Tests that the bounded-concurrency fetch window is respected."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from aiohttp import ClientSession

from dep_rank.core.rate_limiter import AdaptiveRateLimiter
from dep_rank.core.scraper import _fetch_page


class _FakeResponse:
    status = 200

    def __init__(self, counter: _Counter) -> None:
        self._counter = counter
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> _FakeResponse:
        self._counter.enter()
        await asyncio.sleep(0.02)  # hold the slot so overlap is observable
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._counter.exit()

    async def read(self) -> bytes:
        return b"<html></html>"


class _Counter:
    def __init__(self) -> None:
        self.current = 0
        self.peak = 0

    def enter(self) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)

    def exit(self) -> None:
        self.current -= 1


class _FakeSession:
    def __init__(self, counter: _Counter) -> None:
        self._counter = counter

    def get(self, url: str, **kwargs: object) -> _FakeResponse:  # noqa: ARG002
        return _FakeResponse(self._counter)


@pytest.mark.asyncio
async def test_fetch_window_never_exceeds_concurrency() -> None:
    concurrency = 3
    counter = _Counter()
    session = cast(ClientSession, _FakeSession(counter))
    # Fast, generous limiter so the semaphore is the only bound under test.
    limiter = AdaptiveRateLimiter(1000, 0.001, concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch(i: int) -> str:
        return await _fetch_page(
            cast(ClientSession, session),
            f"https://github.com/o/r/network/dependents?page={i}",
            limiter,
            semaphore,
            {},
            None,
        )

    await asyncio.gather(*(fetch(i) for i in range(12)))
    assert counter.peak <= concurrency
    assert counter.peak >= 2  # sanity: some overlap actually happened
