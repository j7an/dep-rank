"""Tests for stale-while-revalidate background refresh."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from aiohttp import ClientSession

from dep_rank.core.cache import SqliteCache
from dep_rank.core.rate_limiter import AdaptiveRateLimiter
from dep_rank.core.scraper import SWRManager


class _FakeResp:
    def __init__(self, status: int, body: bytes = b"", etag: str | None = None, delay: float = 0.0):
        self.status = status
        self._body = body
        self.headers: dict[str, str] = {"ETag": etag} if etag else {}
        self._delay = delay

    async def __aenter__(self) -> _FakeResp:
        if self._delay:
            await asyncio.sleep(self._delay)
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


def _auth_limiter() -> AdaptiveRateLimiter:
    return AdaptiveRateLimiter(60, 60.0, 3)


async def _seed_expired(cache: SqliteCache, url: str, body: bytes, etag: str) -> None:
    await cache.put(url, body, etag=etag, ttl=-1)  # already expired


@pytest.fixture
async def cache(tmp_path: Any) -> SqliteCache:
    c = SqliteCache(str(tmp_path))
    await c.initialize()
    return c


URL = "https://github.com/o/r/network/dependents?page=5"


class TestSWRManager:
    @pytest.mark.asyncio
    async def test_disabled_when_unauthenticated(self, cache: SqliteCache) -> None:
        fake = _FakeSession([])
        session = cast(ClientSession, fake)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=False)
        swr.schedule(URL)
        await swr.drain()
        assert fake.calls == 0  # no refresh ever scheduled

    @pytest.mark.asyncio
    async def test_refresh_updates_cache_on_200(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = cast(ClientSession, _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"')]))
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"fresh"
        assert entry["expired"] is False

    @pytest.mark.asyncio
    async def test_refresh_bumps_ttl_on_304(self, cache: SqliteCache) -> None:
        """A 304 revalidation keeps the stale body but refreshes its TTL (no longer expired)."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        fake = _FakeSession([_FakeResp(304)])
        session = cast(ClientSession, fake)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert fake.calls == 1
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"  # body unchanged on 304
        assert entry["expired"] is False  # TTL bumped

    @pytest.mark.asyncio
    async def test_429_feeds_aimd_then_suppresses(self, cache: SqliteCache) -> None:
        """A background 429 must update the shared limiter (AIMD halves concurrency),
        leave the stale body intact, and — once concurrency hits the floor — suppress
        further background refreshes."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = AdaptiveRateLimiter(60, 60.0, concurrency=2, jitter=False)
        assert limiter.current_max_concurrency == 2
        resp = _FakeResp(429)
        resp.headers["Retry-After"] = "30"
        fake = _FakeSession([resp])
        session = cast(ClientSession, fake)
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        swr.schedule(URL)
        await swr.drain()
        assert fake.calls == 1
        # AIMD consumed the background 429: 2 -> 1.
        assert limiter.current_max_concurrency == 1
        # The 429 did not overwrite the cached stale body.
        entry = await cache.get(URL)
        assert entry is not None and entry["body"] == b"stale"
        # With concurrency at the floor, a fresh URL's refresh is now suppressed —
        # if it were not, _FakeSession.get would pop an empty queue and raise.
        other = URL + "&x=2"
        await _seed_expired(cache, other, b"stale2", '"old2"')
        swr.schedule(other)
        await swr.drain()
        assert fake.calls == 1  # no new request fired

    @pytest.mark.asyncio
    async def test_dedup_one_refresh_per_url(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        fake = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"', delay=0.02)])
        session = cast(ClientSession, fake)
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        swr.schedule(URL)  # second call must be a no-op (already in flight)
        await swr.drain()
        assert fake.calls == 1

    @pytest.mark.asyncio
    async def test_failed_refresh_enters_cooldown(
        self, cache: SqliteCache, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        clock = {"t": 0.0}
        await _seed_expired(cache, URL, b"stale", '"old"')
        fake = _FakeSession([_FakeResp(500)])  # one failing response only
        session = cast(ClientSession, fake)
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
        assert fake.calls == 1  # failed once
        # The failure is surfaced at WARNING (spec §3 "Logged at WARNING; silent to user").
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        # within cooldown: a second schedule must NOT fire another request
        swr.schedule(URL)
        await swr.drain()
        assert fake.calls == 1

    @pytest.mark.asyncio
    async def test_no_refresh_without_foreground_headroom(self, cache: SqliteCache) -> None:
        """Spec §3 foreground-priority: at low headroom the refresh makes no request AND
        a concurrent foreground ``acquire()`` is not delayed by the refresh path."""
        await _seed_expired(cache, URL, b"stale", '"old"')
        limiter = AdaptiveRateLimiter(60, 60.0, 3)
        # Drain to exactly 1 token: below SWR headroom (a refresh needs >=2 via
        # try_acquire(reserve=1)) but the foreground can still take its single token.
        while limiter.tokens_available() >= 2:
            limiter.try_acquire()
        fake = _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"')])
        session = cast(ClientSession, fake)
        swr = SWRManager(session, limiter, {}, cache, enabled=True)

        # Run the background refresh and a foreground acquire concurrently. The refresh
        # must abort (no request); the foreground acquire must complete promptly rather
        # than blocking on a token the refresh grabbed or a 60s bucket sleep. If the
        # foreground were starved, wait_for would raise TimeoutError well before 60s.
        swr.schedule(URL)
        foreground = asyncio.create_task(limiter.acquire())
        await asyncio.wait_for(asyncio.gather(swr.drain(), foreground), timeout=1.0)

        assert fake.calls == 0  # refresh aborted: try_acquire(reserve=1) saw <2 tokens
        assert foreground.done()  # foreground acquired immediately, never queued behind SWR

    @pytest.mark.asyncio
    async def test_drain_cancels_stragglers_past_timeout(self, cache: SqliteCache) -> None:
        await _seed_expired(cache, URL, b"stale", '"old"')
        session = cast(ClientSession, _FakeSession([_FakeResp(200, body=b"fresh", etag='"new"', delay=5.0)]))
        swr = SWRManager(session, _auth_limiter(), {}, cache, enabled=True)
        swr.schedule(URL)
        # Drain with a tiny timeout: must return promptly, cancelling the slow refresh.
        await swr.drain(timeout=0.05)
        # Cache still holds the stale body (refresh was cancelled before writing).
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"stale"


class TestSWRIntegration:
    @pytest.mark.asyncio
    async def test_read_page_serves_stale_and_schedules_refresh(self, cache: SqliteCache) -> None:
        from dep_rank.core.scraper import _read_page

        await _seed_expired(cache, URL, b"<html>stale</html>", '"old"')
        fake = _FakeSession([_FakeResp(200, body=b"<html>fresh</html>", etag='"new"')])
        session = cast(ClientSession, fake)
        limiter = _auth_limiter()
        swr = SWRManager(session, limiter, {}, cache, enabled=True)
        html = await _read_page(
            session,
            URL,
            limiter,
            asyncio.Semaphore(3),
            {},
            cache,
            swr=swr,
        )
        assert html == "<html>stale</html>"  # stale served synchronously
        await swr.drain()
        entry = await cache.get(URL)
        assert entry is not None
        assert entry["body"] == b"<html>fresh</html>"  # refreshed in background

    @pytest.mark.asyncio
    async def test_stream_blocks_on_drain_before_returning(self, tmp_path: Any) -> None:
        """`stream_dependents` must AWAIT `swr.drain()` in its finally *before returning* —
        not leave the refresh as a fire-and-forget task that merely happens to finish in
        time.

        The trap this avoids: with an immediate refresh response, the task can complete
        opportunistically while the wrapper is still consuming snapshots, so a "is the
        entry refreshed by the time scrape returns?" assertion passes *even if the
        generator never drained*. So we make completion-without-drain impossible: the
        background refresh response is **delayed**, while the foreground walk is a single
        stale cache-hit (no foreground fetch) that would return near-instantly on its own.

        - If the generator drains: the scrape return is BLOCKED until the delayed 304 lands
          and bumps the TTL, so the entry is no longer expired when scrape returns.
        - If it does NOT drain: scrape returns while the refresh is still mid-delay, the
          entry is still expired, and this test fails — catching the exact lifecycle bug
          (refresh deferred past the caller's `async with session` exit) the design fixes.
        """
        from dep_rank.core.scraper import scrape_dependents

        cache = SqliteCache(str(tmp_path))
        await cache.initialize()
        first = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
        stale = (
            '<html><body><div class="table-list-header-toggle states flex-auto pl-0">'
            '<a class="btn-link selected" '
            'href="/owner/repo/network/dependents?dependent_type=REPOSITORY">30 Repositories</a>'
            '</div><div id="dependents"><div class="Box">'
            '<div class="flex-items-center"><span>'
            '<a class="text-bold" href="/a/one">a/one</a></span><div><span>100</span></div></div>'
            '</div><div class="paginate-container"><div>'
            '<a href="/owner/repo/network/dependents?page=0">Previous</a></div></div></div>'
            "</body></html>"
        )
        await cache.put(first, stale.encode(), etag='"old"', ttl=-1)  # expired
        # Page 1 is served from the stale cache (no foreground fetch), so the ONLY
        # session.get() is the delayed background refresh. The 0.3s delay (< the 10s drain
        # timeout, so it is not cancelled) is what makes "completed by return" equivalent
        # to "return was blocked on drain": on a single event loop the fast foreground walk
        # cannot outrun a still-sleeping refresh task.
        fake = _FakeSession([_FakeResp(304, delay=0.3)])
        session = cast(ClientSession, fake)
        try:
            result = await scrape_dependents(
                session,
                "https://github.com/owner/repo",
                rows=5,
                token="ghp_x",
                cache=cache,
            )
            assert result.complete is True
            assert [r.name for r in result.repos] == ["one"]
            assert fake.calls == 1  # the background refresh actually ran
            # Refreshed-by-return is only possible if the return blocked on drain; a
            # fire-and-forget task would still be mid-delay at this point.
            refreshed = await cache.get(first)
            assert refreshed is not None
            assert refreshed["expired"] is False
        finally:
            await cache.close()
