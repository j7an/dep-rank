"""Tests for stale-while-revalidate background refresh."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import pytest
import yarl
from aiohttp import ClientSession
from aioresponses import CallbackResult, aioresponses

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import ScrapeSnapshot
from dep_rank.core.rate_limiter import RateLimiter
from dep_rank.core.scraper import SWRManager, _read_page, scrape_dependents
from tests.conftest import dependents_page


def _auth_limiter() -> RateLimiter:
    return RateLimiter(60, 60.0)


async def _seed_expired(cache: SqliteCache, url: str, body: bytes, etag: str) -> None:
    await cache.put(url, body, etag=etag, ttl=-1)  # already expired


URL = "https://github.com/o/r/network/dependents?page=5"
FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
STALE_PAGE = dependents_page([("a", "one", 100)], repos=30)


class TestSWRManager:
    async def test_disabled_when_unauthenticated(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=False)
        swr.schedule(URL)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 0  # no refresh ever scheduled

    async def test_refresh_updates_cache_on_200(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        mock_http.get(URL, status=200, body=b"fresh", headers={"ETag": '"new"'})
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"fresh"
        assert entry["expired"] is False

    async def test_refresh_bumps_ttl_on_304(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        """A 304 revalidation keeps the stale body but refreshes its TTL (no longer expired)."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        mock_http.get(URL, status=304)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"  # body unchanged on 304
        assert entry["expired"] is False  # TTL bumped

    async def test_429_pauses_background_refresh(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        """A background 429 pauses further refreshes and preserves the stale body."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = RateLimiter(60, 60.0)
        mock_http.get(URL, status=429, headers={"Retry-After": "30"})
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1
        # The shared limiter consumed the background 429.
        assert limiter.background_paused() is True
        # The 429 did not overwrite the cached stale body.
        entry = await cache.get(URL)
        assert entry is not None and entry["body"] == b"stale"
        # While background work is paused, a fresh URL's refresh is suppressed —
        # Count recorded attempts so an unmatched request cannot hide a regression.
        other = URL + "&x=2"
        await _seed_expired(cache, other, b"stale2", '"old2"')
        swr.schedule(other)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1  # no new request fired

    async def test_queued_refresh_skips_after_429(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        other = URL + "&x=2"
        await _seed_expired(cache, URL, b"stale", '"old"')
        await _seed_expired(cache, other, b"stale", '"old2"')

        async def delayed_429(url: yarl.URL, **kwargs: Any) -> CallbackResult:
            await asyncio.sleep(0.01)
            return CallbackResult(status=429)

        mock_http.get(URL, callback=delayed_429)
        mock_http.get(other, status=200, body=b"fresh")
        limiter = RateLimiter(3, 60.0, now=lambda: 1000.0)
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        swr.schedule(URL)
        swr.schedule(other)
        await swr.drain()
        assert limiter.try_acquire(reserve=1) is True
        entry = await cache.get(other)
        assert sum(len(v) for v in mock_http.requests.values()) == 1
        assert entry is not None
        assert entry["body"] == b"stale"

    async def test_foreground_429_during_cache_lookup_blocks_refresh(
        self,
        cache: SqliteCache,
        mock_http: aioresponses,
        session: ClientSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = RateLimiter(60, 60.0)
        real_get = cache.get

        async def get_then_429(url: str) -> dict[str, Any] | None:
            entry = await real_get(url)
            limiter.note_429()
            return entry

        monkeypatch.setattr(cache, "get", get_then_429)
        mock_http.get(URL, status=200, body=b"fresh")
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 0

    async def test_dedup_one_refresh_per_url(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')

        async def delayed_200(url: yarl.URL, **kwargs: Any) -> CallbackResult:
            await asyncio.sleep(0.02)
            return CallbackResult(status=200, body=b"fresh", headers={"ETag": '"new"'})

        mock_http.get(URL, callback=delayed_200)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        swr.schedule(URL)  # second call must be a no-op (already in flight)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1

    async def test_failed_refresh_enters_cooldown(
        self,
        cache: SqliteCache,
        mock_http: aioresponses,
        session: ClientSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        clock = {"t": 0.0}
        await _seed_expired(cache, URL, b"stale", '"old"')
        mock_http.get(URL, status=500)  # one failing response only
        swr = SWRManager(
            session,
            _auth_limiter(),
            {},
            cache,
            enabled=True,
            now=lambda: clock["t"],
        )
        with caplog.at_level(logging.WARNING, logger="dep_rank.core.scraper"):
            swr.schedule(URL)
            await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1  # failed once
        # The failure is surfaced at WARNING (spec §3 "Logged at WARNING; silent to user").
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        # within cooldown: a second schedule must NOT fire another request
        swr.schedule(URL)
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1

    async def test_transport_error_enters_cooldown(
        self,
        cache: SqliteCache,
        mock_http: aioresponses,
        session: ClientSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        mock_http.get(URL, exception=aiohttp.ClientConnectionError())
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        with caplog.at_level(logging.WARNING, logger="dep_rank.core.scraper"):
            swr.schedule(URL)
            await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1
        assert any("ClientConnectionError" in r.getMessage() for r in caplog.records)
        swr.schedule(URL)  # within cooldown: no second request
        await swr.drain()
        assert sum(len(v) for v in mock_http.requests.values()) == 1

    async def test_no_refresh_without_foreground_headroom(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        """Spec §3 foreground-priority: at low headroom the refresh makes no request AND
        a concurrent foreground ``acquire()`` is not delayed by the refresh path."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = RateLimiter(60, 60.0)
        # Drain to exactly 1 token: below SWR headroom (a refresh needs >=2 via
        # try_acquire(reserve=1)) but the foreground can still take its single token.
        while limiter.try_acquire(reserve=1):
            pass
        mock_http.get(URL, status=200, body=b"fresh", headers={"ETag": '"new"'})
        swr = SWRManager(session, limiter, {}, cache, enabled=True)

        # Run the background refresh and a foreground acquire concurrently. The refresh
        # must abort (no request); the foreground acquire must complete promptly rather
        # than blocking on a token the refresh grabbed or a 60s bucket sleep. If the
        # foreground were starved, wait_for would raise TimeoutError well before 60s.
        swr.schedule(URL)
        foreground = asyncio.create_task(limiter.acquire())
        await asyncio.wait_for(asyncio.gather(swr.drain(), foreground), timeout=1.0)

        assert (
            sum(len(v) for v in mock_http.requests.values()) == 0
        )  # refresh aborted: try_acquire(reserve=1) saw <2 tokens
        assert foreground.done()  # foreground acquired immediately, never queued behind SWR

    async def test_drain_cancels_stragglers_past_timeout(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')

        async def delayed_200(url: yarl.URL, **kwargs: Any) -> CallbackResult:
            await asyncio.sleep(5.0)
            return CallbackResult(status=200, body=b"fresh", headers={"ETag": '"new"'})

        mock_http.get(URL, callback=delayed_200)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        # Drain with a tiny timeout: must return promptly, cancelling the slow refresh.
        await swr.drain(timeout=0.05)
        # Cache still holds the stale body (refresh was cancelled before writing).
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"


class TestSWRIntegration:
    async def test_read_page_serves_stale_and_schedules_refresh(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        await _seed_expired(cache, URL, b"<html>stale</html>", '"old"')
        mock_http.get(URL, status=200, body=b"<html>fresh</html>", headers={"ETag": '"new"'})
        limiter = _auth_limiter()
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        html = await _read_page(session, URL, limiter, {}, cache, swr)
        assert html == "<html>stale</html>"  # stale served synchronously
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"<html>fresh</html>"  # refreshed in background

    async def test_stream_blocks_on_drain_before_returning(
        self, cache: SqliteCache, mock_http: aioresponses, session: ClientSession
    ) -> None:
        """`scrape_dependents` must AWAIT `swr.drain()` in its finally *before returning* —
        not leave the refresh as a fire-and-forget task that merely happens to finish in
        time.

        The trap this avoids: with an immediate refresh response, the task can complete
        opportunistically while the scrape is still walking pages, so a "is the
        entry refreshed by the time scrape returns?" assertion passes *even if the
        scrape never drained*. So we make completion-without-drain impossible: the
        background refresh response is **delayed**, while the foreground walk is a single
        stale cache-hit (no foreground fetch) that would return near-instantly on its own.

        - If the scrape drains: the scrape return is BLOCKED until the delayed 304 lands
          and bumps the TTL, so the entry is no longer expired when scrape returns.
        - If it does NOT drain: scrape returns while the refresh is still mid-delay, the
          entry is still expired, and this test fails — catching the exact lifecycle bug
          (refresh deferred past the caller's `async with session` exit) the design fixes.
        """
        await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)  # expired

        # Page 1 is served from the stale cache (no foreground fetch), so the ONLY
        # session.get() is the delayed background refresh. The 0.3s delay (< the 10s drain
        # timeout, so it is not cancelled) is what makes "completed by return" equivalent
        # to "return was blocked on drain": on a single event loop the fast foreground walk
        # cannot outrun a still-sleeping refresh task.
        async def delayed_304(url: yarl.URL, **kwargs: Any) -> CallbackResult:
            await asyncio.sleep(0.3)
            return CallbackResult(status=304)

        mock_http.get(FIRST, callback=delayed_304)
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            rows=5,
            token="ghp_x",
            cache=cache,
        )
        assert result.complete is True
        assert [r.name for r in result.repos] == ["one"]
        assert (
            sum(len(v) for v in mock_http.requests.values()) == 1
        )  # the background refresh actually ran
        # Refreshed-by-return is only possible if the return blocked on drain; a
        # fire-and-forget task would still be mid-delay at this point.
        refreshed = await cache.get(FIRST)
        assert refreshed is not None
        assert refreshed["expired"] is False


async def test_unauthenticated_expired_hit_serves_stale_without_request(
    cache: SqliteCache, mock_http: aioresponses, session: ClientSession
) -> None:
    await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)
    result = await scrape_dependents(session, "https://github.com/owner/repo", rows=5, cache=cache)
    assert [r.name for r in result.repos] == ["one"]
    assert sum(len(v) for v in mock_http.requests.values()) == 0


async def test_on_page_exception_still_drains_refresh(
    cache: SqliteCache, mock_http: aioresponses, session: ClientSession
) -> None:
    await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)

    async def delayed_304(url: yarl.URL, **kwargs: Any) -> CallbackResult:
        await asyncio.sleep(0.3)
        return CallbackResult(status=304)

    mock_http.get(FIRST, callback=delayed_304)

    async def on_page(snap: ScrapeSnapshot) -> None:
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            rows=5,
            token="ghp_x",
            cache=cache,
            on_page=on_page,
        )
    assert sum(len(v) for v in mock_http.requests.values()) == 1
    refreshed = await cache.get(FIRST)
    assert refreshed is not None
    assert refreshed["expired"] is False  # the delayed refresh finished before the raise
