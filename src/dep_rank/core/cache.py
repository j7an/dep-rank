"""Thin SQLite HTTP cache wrapper over aiosqlite."""

from __future__ import annotations

import os
import time
from typing import Any

import aiosqlite


class SqliteCache:
    """Async SQLite-backed HTTP cache with ETag and TTL support."""

    def __init__(self, cache_dir: str) -> None:
        os.makedirs(cache_dir, exist_ok=True)
        self._db_path = os.path.join(cache_dir, "http_cache.db")
        self._db: aiosqlite.Connection | None = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        return self._db

    async def initialize(self) -> None:
        """Create the cache table if it doesn't exist."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY,
                etag TEXT,
                body BLOB,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        await self._db.commit()

    async def get(self, url: str) -> dict[str, Any] | None:
        """Return {"body", "etag", "expired"}, or None on a cache miss.

        Expired rows are returned so the caller can serve stale content and
        send If-None-Match.
        """
        cursor = await self._conn.execute(
            "SELECT body, etag, expires_at FROM http_cache WHERE url = ?", (url,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        body, etag, expires_at = row
        expired = time.time() >= expires_at
        return {"body": body, "etag": etag, "expired": expired}

    async def put(self, url: str, body: bytes, etag: str | None, ttl: int) -> None:
        """Store a response in the cache."""
        now = time.time()
        await self._conn.execute(
            """INSERT OR REPLACE INTO http_cache (url, etag, body, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (url, etag, body, now, now + ttl),
        )
        await self._conn.commit()

    async def clear(self) -> None:
        """Delete all cached entries."""
        await self._conn.execute("DELETE FROM http_cache")
        await self._conn.commit()

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        cursor = await self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM http_cache"
        )
        entries, size = await cursor.fetchone() or (0, 0)
        return {"entries": entries, "size_bytes": size}

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
