"""Tests for SQLite HTTP cache."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dep_rank.core.cache import SqliteCache


class TestSqliteCache:
    async def test_get_miss(self, cache: SqliteCache) -> None:
        result = await cache.get("https://example.com/missing")
        assert result is None

    async def test_put_and_get(self, cache: SqliteCache) -> None:
        await cache.put(
            url="https://example.com/page",
            body=b"<html>hello</html>",
            etag='"abc123"',
            ttl=3600,
        )
        result = await cache.get("https://example.com/page")
        assert result is not None
        assert result["body"] == b"<html>hello</html>"
        assert result["etag"] == '"abc123"'
        assert result["expired"] is False

    async def test_expired_entry_returns_body_and_expired_flag(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/old", body=b"old", etag='"old"', ttl=-1)
        result = await cache.get("https://example.com/old")
        assert result is not None
        assert result["body"] == b"old"
        assert result["etag"] == '"old"'
        assert result["expired"] is True

    async def test_put_overwrites(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/x", body=b"v1", etag='"e1"', ttl=3600)
        await cache.put(url="https://example.com/x", body=b"v2", etag='"e2"', ttl=3600)
        result = await cache.get("https://example.com/x")
        assert result is not None
        assert result["body"] == b"v2"

    async def test_clear(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/a", body=b"a", etag=None, ttl=3600)
        await cache.clear()
        result = await cache.get("https://example.com/a")
        assert result is None

    async def test_stats_empty(self, cache: SqliteCache) -> None:
        assert await cache.stats() == {"entries": 0, "size_bytes": 0}

    async def test_stats(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/1", body=b"x", etag=None, ttl=3600)
        await cache.put(url="https://example.com/2", body=b"y", etag=None, ttl=3600)
        stats = await cache.stats()
        assert stats["entries"] == 2
        assert stats["size_bytes"] > 0

    async def test_concurrent_operations(self, cache: SqliteCache) -> None:
        """Concurrent callers (foreground walk + SWR refresh) share one connection safely."""
        urls = [f"https://example.com/{i}" for i in range(20)]
        await asyncio.gather(*(cache.put(url, url.encode(), etag=None, ttl=3600) for url in urls))
        entries = await asyncio.gather(*(cache.get(url) for url in urls))
        assert [e["body"] for e in entries if e] == [url.encode() for url in urls]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda c: c.get("https://example.com"), id="get"),
        pytest.param(lambda c: c.put("https://example.com", b"data", None, 3600), id="put"),
        pytest.param(lambda c: c.clear(), id="clear"),
        pytest.param(lambda c: c.stats(), id="stats"),
    ],
)
async def test_operation_raises_without_init(
    call: Callable[[SqliteCache], Awaitable[object]], tmp_path: Any
) -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await call(SqliteCache(str(tmp_path)))


async def test_close_without_init(tmp_path: Any) -> None:
    await SqliteCache(str(tmp_path)).close()  # must not raise


async def test_close_after_cancelled_initialize_keeps_loop_responsive(tmp_path: Any) -> None:
    """close() waits for a worker stuck on a database lock without blocking the loop."""
    blocker = sqlite3.connect(os.path.join(tmp_path, "http_cache.db"), isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    cache = SqliteCache(str(tmp_path))
    init = asyncio.create_task(cache.initialize())
    await asyncio.sleep(0.05)  # the worker is now waiting on the lock inside connect()
    init.cancel()
    await asyncio.wait({init})
    assert init.cancelled()
    close = asyncio.create_task(cache.close())
    await asyncio.sleep(0.05)  # a blocked loop would not get here until close() finished
    assert not close.done()
    blocker.execute("ROLLBACK")
    blocker.close()
    await asyncio.wait_for(close, timeout=5)
