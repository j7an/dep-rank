"""Shared test fixtures for dep-rank."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import httpx2
import pytest

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import Repository
from dep_rank.core.rate_limiter import RateLimiter

_Respond = Callable[[httpx2.Request], Awaitable[httpx2.Response]]


class FakeHTTP:
    """Queue deterministic responses for the suite’s HTTP requests."""

    def __init__(self) -> None:
        self._responses: dict[tuple[str, str], deque[_Respond]] = defaultdict(deque)
        self.requests: dict[tuple[str, str], list[httpx2.Request]] = defaultdict(list)

    @property
    def request_count(self) -> int:
        return sum(len(calls) for calls in self.requests.values())

    def get(self, url: str, **spec: Any) -> None:
        self._register("GET", url, **spec)

    def post(self, url: str, **spec: Any) -> None:
        self._register("POST", url, **spec)

    def _register(
        self,
        method: str,
        url: str,
        *,
        status: int = 200,
        body: bytes | str | None = None,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
        callback: _Respond | None = None,
        exception: BaseException | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        if content_type is not None:
            response_headers["Content-Type"] = content_type

        async def respond(request: httpx2.Request) -> httpx2.Response:
            if exception is not None:
                raise exception
            if callback is not None:
                return await callback(request)
            return httpx2.Response(status, content=body, json=payload, headers=response_headers)

        self._responses[(method, str(httpx2.URL(url)))].append(respond)

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        key = (request.method, str(request.url))
        self.requests[key].append(request)
        if not self._responses[key]:
            raise httpx2.ConnectError(f"No registered response for {key}", request=request)
        return await self._responses[key].popleft()(request)


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
# GitHub's empty state: a parsed 0/0 header, a blankslate, no rows, no pagination.
DEPENDENTS_HTML_EMPTY = """
<html><body>
<div class="table-list-header-toggle states flex-auto pl-0">
    <a class="btn-link selected"
       href="/owner/repo/network/dependents?dependent_type=REPOSITORY">0 Repositories</a>
    <a class="btn-link " href="/owner/repo/network/dependents?dependent_type=PACKAGE">0 Packages</a>
</div>
<div class="blankslate-container"><div class="blankslate blankslate-spacious">
    <h2 class="blankslate-heading">We haven’t found any dependents for this repository yet.</h2>
</div></div>
</body></html>
"""
# Rows with no count header: the header layout changed (drift), so the total is unknown.
DEPENDENTS_HTML_NO_HEADER = """
<html><body>
<div id="dependents"><div class="Box">
    <div class="flex-items-center">
        <span><a class="text-bold" href="/delta/app">delta/app</a></span>
        <div><div><span>80</span></div></div>
    </div>
</div>
<div class="paginate-container"><div>
    <a href="/owner/repo/network/dependents?page=1">Previous</a>
</div></div>
</div>
</body></html>
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DEP_RANK_TOKEN is not leaked between tests."""
    monkeypatch.delenv("DEP_RANK_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _isolated_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test off the real user cache directory."""
    monkeypatch.setattr("dep_rank.core.cache.default_cache_dir", lambda: str(tmp_path / "cache"))


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


class StalledBody(httpx2.AsyncByteStream):
    """Fail if a response body is read before its status is handled."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx2.ReadTimeout("slow")
        yield b""  # pragma: no cover


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHTTP]:
    """Intercept all clients constructed during a test."""
    fake = FakeHTTP()
    monkeypatch.setattr(
        httpx2, "AsyncClient", partial(httpx2.AsyncClient, transport=httpx2.MockTransport(fake))
    )
    yield fake


@pytest.fixture
async def session(mock_http: FakeHTTP) -> AsyncIterator[httpx2.AsyncClient]:
    """Close the HTTP client before its request mock is removed."""
    async with httpx2.AsyncClient() as instance:
        yield instance
