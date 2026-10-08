"""Shared test fixtures for dep-rank."""

from __future__ import annotations

import inspect
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import aiohttp
import httpx2
import pytest
from aioresponses import aioresponses

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import Repository
from dep_rank.core.rate_limiter import RateLimiter

# aiohttp 3.14 added a required keyword-only ``stream_writer`` argument to
# ``ClientResponse.__init__``. aioresponses (<=0.7.8) builds mocked responses
# without it, so every mocked request raises ``TypeError: ... missing 1
# required keyword-only argument: 'stream_writer'``. aiohttp only reads
# ``stream_writer.output_size``, so a ``Mock(output_size=0)`` suffices.
#
# This mirrors the upstream fix (aioresponses#288, tracking aioresponses#289).
# The signature guard makes it a no-op on aiohttp < 3.14 and once aioresponses
# ships a release that supplies the argument itself; remove this shim then.
_response_init = aiohttp.ClientResponse.__init__
if "stream_writer" in inspect.signature(_response_init).parameters:

    def _patched_response_init(self: aiohttp.ClientResponse, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stream_writer", Mock(output_size=0))
        _response_init(self, *args, **kwargs)

    # aiohttp's constructor is an overloaded method; this test-only compatibility shim
    # deliberately accepts its complete call surface to add the missing keyword.
    aiohttp.ClientResponse.__init__ = _patched_response_init  # type: ignore[method-assign]


_ResponseSpec = tuple[
    int,
    bytes | str | None,
    Any,
    dict[str, str],
    Callable[[httpx2.Request], Awaitable[httpx2.Response]] | None,
    BaseException | None,
]


class FakeHTTP:
    """Mirror only the aioresponses features the suite uses."""

    def __init__(self) -> None:
        self._responses: dict[tuple[str, str], deque[_ResponseSpec]] = defaultdict(deque)
        self.requests: dict[tuple[str, str], list[httpx2.Request]] = defaultdict(list)

    def get(
        self,
        url: str,
        *,
        status: int = 200,
        body: bytes | str | None = None,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
        callback: Callable[[httpx2.Request], Awaitable[httpx2.Response]] | None = None,
        exception: BaseException | None = None,
    ) -> None:
        self._register(
            "GET", url, status, body, payload, headers, content_type, callback, exception
        )

    def post(
        self,
        url: str,
        *,
        status: int = 200,
        body: bytes | str | None = None,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
        callback: Callable[[httpx2.Request], Awaitable[httpx2.Response]] | None = None,
        exception: BaseException | None = None,
    ) -> None:
        self._register(
            "POST", url, status, body, payload, headers, content_type, callback, exception
        )

    def _register(
        self,
        method: str,
        url: str,
        status: int,
        body: bytes | str | None,
        payload: Any,
        headers: dict[str, str] | None,
        content_type: str | None,
        callback: Callable[[httpx2.Request], Awaitable[httpx2.Response]] | None,
        exception: BaseException | None,
    ) -> None:
        response_headers = dict(headers or {})
        if content_type is not None:
            response_headers["Content-Type"] = content_type
        key = (method, str(httpx2.URL(url)))
        self._responses[key].append((status, body, payload, response_headers, callback, exception))

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        key = (request.method, str(request.url))
        self.requests[key].append(request)
        if not self._responses[key]:
            raise httpx2.ConnectError(f"No registered response for {key}", request=request)
        status, body, payload, headers, callback, exception = self._responses[key].popleft()
        if exception is not None:
            raise exception
        if callback is not None:
            return await callback(request)
        return httpx2.Response(status, content=body, json=payload, headers=headers)


def dependents_page(
    items: Sequence[tuple[str, str, int]],
    *,
    next_page: int | None = None,
    repos: int = 90,
    packages: int | None = None,
) -> str:
    """Build a valid dependents page with counts, repository rows, and pagination."""
    package_link = (
        f'<a class="btn-link" href="/owner/repo/network/dependents?dependent_type=PACKAGE">'
        f"{packages} Packages</a>"
        if packages is not None
        else ""
    )
    rows = "".join(
        f'<div class="flex-items-center">'
        f'<span><a class="text-bold" href="/{owner}/{name}">{owner}/{name}</a></span>'
        f"<div><span>{stars:,}</span></div></div>"
        for owner, name, stars in items
    )
    nav = (
        f'<a href="/owner/repo/network/dependents?page={next_page}">Next</a>'
        if next_page is not None
        else '<a href="/owner/repo/network/dependents?page=1">Previous</a>'
    )
    return f"""
    <html><body>
    <div class="table-list-header-toggle states flex-auto pl-0">
        <a class="btn-link selected"
           href="/owner/repo/network/dependents?dependent_type=REPOSITORY">{repos} Repositories</a>
        {package_link}
    </div>
    <div id="dependents"><div class="Box">{rows}</div>
    <div class="paginate-container"><div>{nav}</div></div></div>
    </body></html>
    """


DEPENDENTS_HTML_PAGE_1 = dependents_page(
    [("alpha", "framework", 12500), ("beta", "toolkit", 3200), ("gamma", "utils", 150)],
    next_page=2,
)
DEPENDENTS_HTML_LAST_PAGE = dependents_page([("delta", "app", 80)])
DEPENDENTS_HTML_NO_RESULTS = dependents_page([], repos=0)
DEPENDENTS_HTML_WITH_COUNTS_PAGE_1 = dependents_page(
    [("alpha", "framework", 12500)], next_page=2, repos=900, packages=150
)
DEPENDENTS_HTML_WITH_COUNTS = dependents_page(
    [("alpha", "framework", 12500)], repos=900, packages=150
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DEP_RANK_TOKEN is not leaked between tests."""
    monkeypatch.delenv("DEP_RANK_TOKEN", raising=False)


@pytest.fixture
async def cache(tmp_path: Path) -> AsyncIterator[SqliteCache]:
    """Initialize an isolated cache and close it after each test."""
    instance = SqliteCache(str(tmp_path))
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def fast_limiter() -> RateLimiter:
    """Avoid throttling in tests focused on pagination rather than request budgets."""
    return RateLimiter(rate=100_000, period=1.0)


def make_repo(owner: str, name: str, stars: int = 100, **fields: Any) -> Repository:
    """Build a repository with its standard GitHub URL and optional model fields."""
    return Repository(
        owner=owner, name=name, url=f"https://github.com/{owner}/{name}", stars=stars, **fields
    )


@pytest.fixture
def mock_http() -> Iterator[aioresponses]:
    """Mock HTTP requests for the duration of a test."""
    with aioresponses() as responses:
        yield responses


@pytest.fixture
async def session(mock_http: aioresponses) -> AsyncIterator[aiohttp.ClientSession]:
    """Close the HTTP session before its request mock is removed."""
    async with aiohttp.ClientSession() as instance:
        yield instance
