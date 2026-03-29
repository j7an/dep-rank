"""Tests for SQLite HTTP cache."""

from __future__ import annotations

from typing import Any

import pytest

from dep_rank.core.cache import SqliteCache


@pytest.fixture
async def cache(tmp_path: Any) -> SqliteCache:
    c = SqliteCache(str(tmp_path))
    await c.initialize()
    return c


class TestSqliteCache:
    @pytest.mark.asyncio
    async def test_get_miss(self, cache: SqliteCache) -> None:
        result = await cache.get("https://example.com/missing")
        assert result is None

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_expired_entry_returns_body_and_expired_flag(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/old", body=b"old", etag='"old"', ttl=-1)
        result = await cache.get("https://example.com/old")
        assert result is not None
        assert result["body"] == b"old"
        assert result["etag"] == '"old"'
        assert result["expired"] is True

    @pytest.mark.asyncio
    async def test_put_overwrites(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/x", body=b"v1", etag='"e1"', ttl=3600)
        await cache.put(url="https://example.com/x", body=b"v2", etag='"e2"', ttl=3600)
        result = await cache.get("https://example.com/x")
        assert result is not None
        assert result["body"] == b"v2"

    @pytest.mark.asyncio
    async def test_clear(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/a", body=b"a", etag=None, ttl=3600)
        await cache.clear()
        result = await cache.get("https://example.com/a")
        assert result is None

    @pytest.mark.asyncio
    async def test_stats(self, cache: SqliteCache) -> None:
        await cache.put(url="https://example.com/1", body=b"x", etag=None, ttl=3600)
        await cache.put(url="https://example.com/2", body=b"y", etag=None, ttl=3600)
        stats = await cache.stats()
        assert stats["entries"] == 2
        assert stats["size_bytes"] > 0


class TestSqliteCacheUninitialized:
    """Test RuntimeError paths when cache is not initialized."""

    def _make_uninit_cache(self, tmp_path: Any) -> SqliteCache:
        c = SqliteCache(str(tmp_path))
        # Do NOT call initialize
        return c

    @pytest.mark.asyncio
    async def test_get_raises_without_init(self, tmp_path: Any) -> None:
        c = self._make_uninit_cache(tmp_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            await c.get("https://example.com")

    @pytest.mark.asyncio
    async def test_put_raises_without_init(self, tmp_path: Any) -> None:
        c = self._make_uninit_cache(tmp_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            await c.put("https://example.com", b"data", None, 3600)

    @pytest.mark.asyncio
    async def test_clear_raises_without_init(self, tmp_path: Any) -> None:
        c = self._make_uninit_cache(tmp_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            await c.clear()

    @pytest.mark.asyncio
    async def test_stats_raises_without_init(self, tmp_path: Any) -> None:
        c = self._make_uninit_cache(tmp_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            await c.stats()

    @pytest.mark.asyncio
    async def test_close_without_init(self, tmp_path: Any) -> None:
        c = self._make_uninit_cache(tmp_path)
        # close on uninitialized cache should not raise
        await c.close()
