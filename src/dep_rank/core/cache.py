"""Thin SQLite HTTP cache over the stdlib ``sqlite3`` module."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

_T = TypeVar("_T")


class SqliteCache:
    """Async SQLite-backed HTTP cache with ETag and TTL support.

    One worker thread owns the connection, so SQLite I/O never blocks the event loop
    and the connection is never used by two threads (``check_same_thread`` enforces it).
    """

    def __init__(self, cache_dir: str) -> None:
        os.makedirs(cache_dir, exist_ok=True)
        self._db_path = os.path.join(cache_dir, "http_cache.db")
        self._db: sqlite3.Connection | None = None
        self._executor: ThreadPoolExecutor | None = None

    @property
    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            msg = "Cache not initialized; call initialize() first"
            raise RuntimeError(msg)
        return self._db

    async def _run(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        conn = self._conn
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, conn)

    async def initialize(self) -> None:
        """Open the connection and create the cache table if it doesn't exist."""

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(self._db_path)
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS http_cache (
                        url TEXT PRIMARY KEY,
                        etag TEXT,
                        body BLOB,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    )
                """)
            return conn

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dep-rank-cache")
        self._db = await asyncio.get_running_loop().run_in_executor(self._executor, connect)

    async def get(self, url: str) -> dict[str, Any] | None:
        """Return {"body", "etag", "expired"}, or None on a cache miss.

        Expired rows are returned so the caller can serve stale content and
        send If-None-Match.
        """
        row = await self._run(
            lambda conn: conn.execute(
                "SELECT body, etag, expires_at FROM http_cache WHERE url = ?", (url,)
            ).fetchone()
        )
        if row is None:
            return None

        body, etag, expires_at = row
        expired = time.time() >= expires_at
        return {"body": body, "etag": etag, "expired": expired}

    async def put(self, url: str, body: bytes, etag: str | None, ttl: int) -> None:
        """Store a response in the cache."""
        now = time.time()

        def put(conn: sqlite3.Connection) -> None:
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO http_cache (url, etag, body, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (url, etag, body, now, now + ttl),
                )

        await self._run(put)

    async def clear(self) -> None:
        """Delete all cached entries."""

        def clear(conn: sqlite3.Connection) -> None:
            with conn:
                conn.execute("DELETE FROM http_cache")

        await self._run(clear)

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        entries, size = await self._run(
            lambda conn: conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM http_cache"
            ).fetchone()
        )
        return {"entries": entries, "size_bytes": size}

    async def close(self) -> None:
        """Close the connection after queued operations finish, then stop the worker."""
        if self._db:
            await self._run(sqlite3.Connection.close)
            self._db = None
        if self._executor:
            self._executor.shutdown()
            self._executor = None
