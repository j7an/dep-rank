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
        """Get a cached response. Returns None on miss.

        If expired but has an etag, returns {"body": None, "etag": etag}
        so the caller can make a conditional request.
        """
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        cursor = await self._db.execute(
            "SELECT body, etag, expires_at FROM http_cache WHERE url = ?", (url,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        body, etag, expires_at = row
        if time.time() < expires_at:
            return {"body": body, "etag": etag}
        # Expired — return etag for conditional request, but no body
        return {"body": None, "etag": etag}

    async def put(self, url: str, body: bytes, etag: str | None, ttl: int) -> None:
        """Store a response in the cache."""
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        now = time.time()
        await self._db.execute(
            """INSERT OR REPLACE INTO http_cache (url, etag, body, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (url, etag, body, now, now + ttl),
        )
        await self._db.commit()

    async def clear(self) -> None:
        """Delete all cached entries."""
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        await self._db.execute("DELETE FROM http_cache")
        await self._db.commit()

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        cursor = await self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM http_cache"
        )
        row = await cursor.fetchone()
        if row is None:
            return {"entries": 0, "size_bytes": 0}
        return {"entries": row[0], "size_bytes": row[1]}

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
