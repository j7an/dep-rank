"""Tests for stale-while-revalidate background refresh."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import aiohttp
import pytest
from aiohttp import ClientSession

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import ScrapeSnapshot
from dep_rank.core.rate_limiter import RateLimiter
from dep_rank.core.scraper import SWRManager, scrape_dependents
from tests.conftest import dependents_page


class _FakeResp:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        etag: str | None = None,
        delay: float = 0.0,
        exc: Exception | None = None,
    ):
        self.status = status
        self._body = body
        self.headers: dict[str, str] = {"ETag": etag} if etag else {}
        self._delay = delay
        self._exc = exc

    async def __aenter__(self) -> _FakeResp:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """Returns queued responses per call; records how many GETs happened."""

    def __init__(self, responses: list[_FakeResp]):
        self._responses = responses
        self.calls = 0

    def get(self, url: str, **kwargs: Any) -> _FakeResp:  # noqa: ARG002
        self.calls += 1
        return self._responses.pop(0)


def _auth_limiter() -> RateLimiter:
    return RateLimiter(60, 60.0)


async def _seed_expired(cache: SqliteCache, url: str, body: bytes, etag: str) -> None:
    await cache.put(url, body, etag=etag, ttl=-1)  # already expired


URL = "https://github.com/o/r/network/dependents?page=5"
FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
STALE_PAGE = dependents_page([("a", "one", 100)], repos=30)


class TestSWRManager:
    async def test_disabled_when_unauthenticated(self, cache: SqliteCache) -> None:
        session = _FakeSession([])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=False)
        swr.schedule(URL)
        await swr.drain()
        assert session.calls == 0  # no refresh ever scheduled

    async def test_refresh_updates_cache_on_200(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"')])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"fresh"
        assert entry["expired"] is False

    async def test_refresh_bumps_ttl_on_304(self, cache: SqliteCache) -> None:
        """A 304 revalidation keeps the stale body but refreshes its TTL (no longer expired)."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(304)])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert session.calls == 1
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"  # body unchanged on 304
        assert entry["expired"] is False  # TTL bumped

    async def test_429_pauses_background_refresh(self, cache: SqliteCache) -> None:
        """A background 429 pauses further refreshes and preserves the stale body."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = RateLimiter(60, 60.0)
        resp = _FakeResp(429)
        resp.headers["Retry-After"] = "30"
        session = _FakeSession([resp])
        swr = SWRManager(cast(ClientSession, session), limiter, {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert session.calls == 1
        # The shared limiter consumed the background 429.
        assert limiter.background_paused() is True
        # The 429 did not overwrite the cached stale body.
        entry = await cache.get(URL)
        assert entry is not None and entry["body"] == b"stale"
        # While background work is paused, a fresh URL's refresh is suppressed —
        # if it were not, _FakeSession.get would pop an empty queue and raise.
        other = URL + "&x=2"
        await _seed_expired(cache, other, b"stale2", '"old2"')
        swr.schedule(other)
        await swr.drain()
        assert session.calls == 1  # no new request fired

    async def test_queued_refresh_skips_after_429(self, cache: SqliteCache) -> None:
        other = URL + "&x=2"
        try:
            await _seed_expired(cache, URL, b"stale", '"old"')
            await _seed_expired(cache, other, b"stale", '"old2"')
            fake = _FakeSession([_FakeResp(429, delay=0.01), _FakeResp(200, body=b"fresh")])
            limiter = RateLimiter(3, 60.0, now=lambda: 1000.0)
            swr = SWRManager(cast(ClientSession, fake), limiter, {}, cache, enabled=True)
            swr.schedule(URL)
            swr.schedule(other)
            await swr.drain()
            assert limiter.try_acquire(reserve=1) is True
            entry = await cache.get(other)
            assert fake.calls == 1
            assert entry is not None
            assert entry["body"] == b"stale"

        finally:
            await cache.close()

    async def test_foreground_429_during_cache_lookup_blocks_refresh(
        self, cache: SqliteCache, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        try:
            await _seed_expired(cache, URL, b"stale", '"old"')
            limiter = RateLimiter(60, 60.0)
            real_get = cache.get

            async def get_then_429(url: str) -> dict[str, Any] | None:
                entry = await real_get(url)
                limiter.note_429()
                return entry

            monkeypatch.setattr(cache, "get", get_then_429)
            fake = _FakeSession([_FakeResp(200, body=b"fresh")])
            swr = SWRManager(cast(ClientSession, fake), limiter, {}, cache, enabled=True)
            swr.schedule(URL)
            await swr.drain()
            assert fake.calls == 0

        finally:
            await cache.close()

    async def test_dedup_one_refresh_per_url(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"', delay=0.02)])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        swr.schedule(URL)  # second call must be a no-op (already in flight)
        await swr.drain()
        assert session.calls == 1

    async def test_failed_refresh_enters_cooldown(
        self, cache: SqliteCache, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        clock = {"t": 0.0}
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(500)])  # one failing response only
        swr = SWRManager(
            cast(ClientSession, session),
            _auth_limiter(),
            {},
            cache,
            enabled=True,
            now=lambda: clock["t"],
        )
        with caplog.at_level(logging.WARNING, logger="dep_rank.core.scraper"):
            swr.schedule(URL)
            await swr.drain()
        assert session.calls == 1  # failed once
        # The failure is surfaced at WARNING (spec §3 "Logged at WARNING; silent to user").
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        # within cooldown: a second schedule must NOT fire another request
        swr.schedule(URL)
        await swr.drain()
        assert session.calls == 1

    async def test_transport_error_enters_cooldown(
        self, cache: SqliteCache, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(0, exc=aiohttp.ClientConnectionError())])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=True)
        with caplog.at_level(logging.WARNING, logger="dep_rank.core.scraper"):
            swr.schedule(URL)
            await swr.drain()
        assert session.calls == 1
        assert any("ClientConnectionError" in r.getMessage() for r in caplog.records)
        swr.schedule(URL)  # within cooldown: no second request
        await swr.drain()
        assert session.calls == 1

    async def test_no_refresh_without_foreground_headroom(self, cache: SqliteCache) -> None:
        """Spec §3 foreground-priority: at low headroom the refresh makes no request AND
        a concurrent foreground ``acquire()`` is not delayed by the refresh path."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = RateLimiter(60, 60.0)
        # Drain to exactly 1 token: below SWR headroom (a refresh needs >=2 via
        # try_acquire(reserve=1)) but the foreground can still take its single token.
        while limiter.try_acquire(reserve=1):
            pass
        session = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"')])
        swr = SWRManager(cast(ClientSession, session), limiter, {}, cache, enabled=True)

        # Run the background refresh and a foreground acquire concurrently. The refresh
        # must abort (no request); the foreground acquire must complete promptly rather
        # than blocking on a token the refresh grabbed or a 60s bucket sleep. If the
        # foreground were starved, wait_for would raise TimeoutError well before 60s.
        swr.schedule(URL)
        foreground = asyncio.create_task(limiter.acquire())
        await asyncio.wait_for(asyncio.gather(swr.drain(), foreground), timeout=1.0)

        assert session.calls == 0  # refresh aborted: try_acquire(reserve=1) saw <2 tokens
        assert foreground.done()  # foreground acquired immediately, never queued behind SWR

    async def test_drain_cancels_stragglers_past_timeout(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"', delay=5.0)])
        swr = SWRManager(cast(ClientSession, session), _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        # Drain with a tiny timeout: must return promptly, cancelling the slow refresh.
        await swr.drain(timeout=0.05)
        # Cache still holds the stale body (refresh was cancelled before writing).
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"


class TestSWRIntegration:
    async def test_read_page_serves_stale_and_schedules_refresh(self, cache: SqliteCache) -> None:
        from dep_rank.core.scraper import _read_page

        await _seed_expired(cache, URL, b"<html>stale</html>", '"old"')
        session = _FakeSession([_FakeResp(200, body=b"<html>fresh</html>", etag='"new"')])
        limiter = _auth_limiter()
        swr = SWRManager(cast(ClientSession, session), limiter, {}, cache, enabled=True)
        html = await _read_page(cast(ClientSession, session), URL, limiter, {}, cache, swr)
        assert html == "<html>stale</html>"  # stale served synchronously
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"<html>fresh</html>"  # refreshed in background

    async def test_stream_blocks_on_drain_before_returning(self, tmp_path: Any) -> None:
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
        cache = SqliteCache(str(tmp_path))
        await cache.initialize()
        await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)  # expired
        # Page 1 is served from the stale cache (no foreground fetch), so the ONLY
        # session.get() is the delayed background refresh. The 0.3s delay (< the 10s drain
        # timeout, so it is not cancelled) is what makes "completed by return" equivalent
        # to "return was blocked on drain": on a single event loop the fast foreground walk
        # cannot outrun a still-sleeping refresh task.
        session = _FakeSession([_FakeResp(304, delay=0.3)])
        try:
            result = await scrape_dependents(
                cast(ClientSession, session),
                "https://github.com/owner/repo",
                rows=5,
                token="ghp_x",
                cache=cache,
            )
            assert result.complete is True
            assert [r.name for r in result.repos] == ["one"]
            assert session.calls == 1  # the background refresh actually ran
            # Refreshed-by-return is only possible if the return blocked on drain; a
            # fire-and-forget task would still be mid-delay at this point.
            refreshed = await cache.get(FIRST)
            assert refreshed is not None
            assert refreshed["expired"] is False
        finally:
            await cache.close()


async def test_unauthenticated_expired_hit_serves_stale_without_request(tmp_path: Any) -> None:
    cache = SqliteCache(str(tmp_path))
    await cache.initialize()
    try:
        await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)
        fake = _FakeSession([])  # any GET would pop an empty list and raise
        result = await scrape_dependents(
            cast(ClientSession, fake), "https://github.com/owner/repo", rows=5, cache=cache
        )
        assert [r.name for r in result.repos] == ["one"]
        assert fake.calls == 0
    finally:
        await cache.close()


async def test_on_page_exception_still_drains_refresh(tmp_path: Any) -> None:
    cache = SqliteCache(str(tmp_path))
    await cache.initialize()
    try:
        await cache.put(FIRST, STALE_PAGE.encode(), etag='"old"', ttl=-1)
        session = _FakeSession([_FakeResp(304, delay=0.3)])

        async def on_page(snap: ScrapeSnapshot) -> None:
            raise RuntimeError("render failed")

        with pytest.raises(RuntimeError, match="render failed"):
            await scrape_dependents(
                cast(ClientSession, session),
                "https://github.com/owner/repo",
                rows=5,
                token="ghp_x",
                cache=cache,
                on_page=on_page,
            )
        assert session.calls == 1
        refreshed = await cache.get(FIRST)
        assert refreshed is not None
        assert refreshed["expired"] is False  # the delayed refresh finished before the raise
    finally:
        await cache.close()
