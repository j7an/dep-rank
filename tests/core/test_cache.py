"""Tests for SQLite HTTP cache."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from dep_rank.core import cache as cache_mod
from dep_rank.core.cache import SqliteCache, default_cache_dir, open_cache


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


class TestDefaultCacheDir:
    @pytest.mark.parametrize(
        ("platform", "case"),
        [
            ("darwin", "darwin"),
            ("linux", "xdg-set"),
            ("linux", "xdg-empty"),
            ("linux", "xdg-unset"),
            ("win32", "win-shell"),
        ],
        ids=["darwin", "xdg-set", "xdg-empty", "xdg-unset", "win-shell"],
    )
    def test_paths(
        self, platform: str, case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert default_cache_dir is not cache_mod.default_cache_dir

        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        home = os.path.expanduser("~")
        if case == "darwin":
            expected = os.path.join(home, "Library", "Caches", "dep-rank")
        elif case == "xdg-set":
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
            expected = os.path.join(str(tmp_path), "dep-rank")
        elif case == "win-shell":
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "env"))
            monkeypatch.setattr(
                "dep_rank.core.cache._win_local_appdata", lambda: str(tmp_path / "shell")
            )
            expected = os.path.join(str(tmp_path / "shell"), "dep-rank", "dep-rank", "Cache")
        else:
            if case == "xdg-empty":
                monkeypatch.setenv("XDG_CACHE_HOME", "")
            expected = os.path.join(home, ".cache", "dep-rank")
        assert default_cache_dir() == expected


class TestOpenCache:
    async def test_closes_on_error(self) -> None:
        held: SqliteCache | None = None
        with pytest.raises(RuntimeError, match="boom"):
            async with open_cache() as cache:
                held = cache
                raise RuntimeError("boom")
        assert held is not None
        with pytest.raises(RuntimeError, match="not initialized"):
            await held.get("x")


async def test_open_cache_uses_default_dir_and_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("dep_rank.core.cache.default_cache_dir", lambda: str(tmp_path / "c"))
    async with open_cache() as opened:
        await opened.put("https://example.com/k", b"v", etag=None, ttl=60)
    assert (tmp_path / "c").is_dir()
    assert opened._db is None  # closed on exit
