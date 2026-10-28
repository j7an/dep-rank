"""FastMCP tool surface for dep-rank; a thin layer over the core pipelines."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Coroutine
from typing import Annotated, Any, Literal, TypeVar

import anyio
import httpx2
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from dep_rank import __version__
from dep_rank.core.cache import open_cache
from dep_rank.core.dependents import get_dependents
from dep_rank.core.models import (
    CodeSearchResult,
    DependentsResult,
    DependentType,
    RetryStatus,
    ScrapeSnapshot,
)
from dep_rank.core.scraper import DEFAULT_MAX_PAGES
from dep_rank.core.search import search_dependents
from dep_rank.core.validation import validate_github_url

_TOKEN_ENV = "DEP_RANK_TOKEN"  # noqa: S105 - env var name, not a credential

INSTRUCTIONS = f"""\
dep-rank finds the repositories that depend on a GitHub repository and ranks them by \
stars or by an optional trust heuristic, and can search code across the top dependents.

- Both tools need a GitHub token in the {_TOKEN_ENV} environment variable of this \
server's process. It is never a tool argument; a call without it fails.
- Long calls report progress, including waits for GitHub rate limits. Calls run one \
at a time, so a call may wait for another to finish first.
- Check `complete` and `reason` before treating counts as totals: when `complete` is \
false, counts are lower bounds over the pages actually scraped.
- Trust scores are pool-relative heuristics computed over the scraped candidates, \
not verdicts on quality or fraud.
"""

_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=True,
    idempotent_hint=False,
    destructive_hint=False,
)

mcp = FastMCP("dep-rank", instructions=INSTRUCTIONS, version=__version__)

# ponytail: tool calls serialize; share one limiter across concurrent walks if queued-call
# timeouts appear in practice.
_LOCK: asyncio.Lock | None = None
_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _lock() -> asyncio.Lock:
    """Return the call lock for the running event loop, creating it on first use per loop."""
    global _LOCK, _LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _LOCK is None or _LOCK_LOOP is not loop:
        _LOCK = asyncio.Lock()
        _LOCK_LOOP = loop
    return _LOCK


_T = TypeVar("_T")


async def _run_with_full_teardown(work: Coroutine[Any, Any, _T]) -> _T:
    """Await ``work`` so that cancelling the request cannot cut its cleanup short.

    MCP cancels a tool call through an anyio cancel scope, which re-cancels every await in
    the handler, including the scraper's ``finally: await swr.drain()`` and the cache and
    HTTP client teardown. ``work`` runs in its own asyncio task instead: on cancellation it
    is cancelled once, and the handler waits, shielded, until its drain, cache and session
    close, and lock release have finished.
    """
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.wait({task})
        raise


async def _acquire_lock(ctx: Context) -> asyncio.Lock:
    """Return the call lock, first telling the client if another call holds it."""
    lock = _lock()
    if lock.locked():
        await ctx.report_progress(0, None, "waiting for another dep-rank call to finish")
    return lock


class _ScrapeProgress:
    """Relays scrape pages and 429 retries to the client as progress notifications.

    Retries are also sent as a warning, for clients that send no progress token.
    """

    def __init__(self, ctx: Context, max_pages: int) -> None:
        self._ctx = ctx
        self._max_pages = max_pages
        self._pages_scraped = 0

    async def on_page(self, snapshot: ScrapeSnapshot) -> None:
        self._pages_scraped = snapshot.pages_scraped
        await self._ctx.report_progress(
            snapshot.pages_scraped,
            self._max_pages,
            f"{snapshot.pages_scraped} pages, ~{snapshot.estimated_total_pages} estimated",
        )

    async def on_retry(self, status: RetryStatus) -> None:
        message = (
            f"GitHub rate limit: retrying page {status.page} in {math.ceil(status.delay)}s "
            f"(attempt {status.attempt}/{status.max_retries})"
        )
        # MCP requires progress to increase with every notification: step a fraction past
        # the last page, staying below the next page's whole number.
        progress = self._pages_scraped + status.attempt / (status.max_retries + 1)
        await self._ctx.report_progress(progress, self._max_pages, message)
        await self._ctx.warning(message)


def _http_client() -> httpx2.AsyncClient:
    headers = {"User-Agent": f"dep-rank/{__version__}"}
    try:
        # Docker MCP Toolkit's --block-network egress proxy is reached via proxy env vars;
        # in httpx2 this covers proxy vars and SSL_CERT_FILE/SSL_CERT_DIR, not .netrc.
        return httpx2.AsyncClient(headers=headers, trust_env=True)
    except ImportError as e:
        # A SOCKS proxy needs socksio, which is not installed. Fail instead of ignoring proxy
        # env, which would silently bypass any HTTP(S) proxy set alongside it.
        msg = (
            "A SOCKS proxy (e.g. ALL_PROXY=socks5://...) is set in the dep-rank MCP server's "
            "environment, and SOCKS proxies are not supported. Remove it from the server's "
            "environment or use an HTTP proxy (HTTP_PROXY/HTTPS_PROXY), then restart the server."
        )
        raise ToolError(msg) from e


def _check_call(url: str) -> str:
    """Return the token, or raise ``ToolError`` if it is missing or ``url`` is invalid.

    Runs before the lock is taken and before any network call.
    """
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        msg = (
            f"{_TOKEN_ENV} is not set in the dep-rank MCP server's environment. "
            "Set it to a GitHub personal access token and restart the server."
        )
        raise ToolError(msg)
    try:
        validate_github_url(url)
    except ValueError as e:
        raise ToolError(str(e)) from e
    return token


@mcp.tool(name="get_dependents", annotations=_ANNOTATIONS)
async def get_dependents_tool(
    url: str,
    ctx: Context,
    rows: Annotated[int, Field(ge=0)] = 10,
    min_stars: Annotated[int, Field(ge=0)] = 5,
    dependent_type: DependentType = DependentType.REPOSITORY,
    descriptions: bool = False,
    rank_by: Literal["stars", "trust"] = "stars",
    trust_check: bool = False,
    downloads: bool = False,
    max_pages: Annotated[int, Field(ge=1, le=1000)] = DEFAULT_MAX_PAGES,
    adaptive_stop: bool = True,
    package_name: str | None = None,
) -> DependentsResult:
    """List a GitHub repository's dependents, ranked by stars or a pool-relative trust score.

    `url` is a github.com repository URL or `owner/repo`. `rows` caps the returned
    repositories. When `complete` is false, counts are lower bounds and `reason` says
    why the scrape stopped. Trust scores are relative to the scraped pool, not verdicts.
    When the repository lists several packages, `package` is the one scraped and `packages`
    lists all names (GitHub shows at most 100); pass `package_name` to scrape another.
    """
    token = _check_call(url)
    progress = _ScrapeProgress(ctx, max_pages)
    lock = await _acquire_lock(ctx)

    async def work() -> DependentsResult:
        async with lock, open_cache() as cache, _http_client() as session:
            return await get_dependents(
                session,
                url,
                rows=rows,
                min_stars=min_stars,
                dependent_type=dependent_type,
                token=token,
                descriptions=descriptions,
                rank_by=rank_by,
                trust_check=trust_check,
                downloads=downloads,
                max_pages=max_pages,
                adaptive_stop=adaptive_stop,
                package_name=package_name,
                cache=cache,
                on_page=progress.on_page,
                on_retry=progress.on_retry,
            )

    try:
        result = await _run_with_full_teardown(work())
    except ValueError as e:
        raise ToolError(str(e)) from e
    if result.ranked_by == "trust" and not result.trust_metadata_complete:
        await ctx.warning(
            "Trust scores used partial repository metadata: some candidates were "
            "scored without forks, issues, pull requests or activity data."
        )
    return result


@mcp.tool(name="search_dependents", annotations=_ANNOTATIONS)
async def search_dependents_tool(
    url: str,
    query: Annotated[str, Field(min_length=1)],
    ctx: Context,
    max_repos: Annotated[int, Field(ge=1)] = 10,
    min_stars: Annotated[int, Field(ge=0)] = 50,
    max_pages: Annotated[int, Field(ge=1, le=1000)] = DEFAULT_MAX_PAGES,
    package_name: str | None = None,
) -> CodeSearchResult:
    """Search code across the top-starred dependents of a GitHub repository.

    `url` is a github.com repository URL or `owner/repo`. The top `max_repos` dependents
    with at least `min_stars` stars are searched with GitHub code search. When `complete`
    is false, the dependent scrape stopped early and `reason` says why. Repos in
    `failed_repos` were not searched; each value says why (for example `HTTP 401` or
    `ConnectError`). Repos in `incomplete_repos` were searched, but GitHub timed out, so
    their hits may be partial.
    When the repository lists several packages, `package` is the one scraped and `packages`
    lists all names (GitHub shows at most 100); pass `package_name` to scrape another.
    """
    token = _check_call(url)
    progress = _ScrapeProgress(ctx, max_pages)
    lock = await _acquire_lock(ctx)

    async def work() -> CodeSearchResult:
        async with lock, open_cache() as cache, _http_client() as session:
            return await search_dependents(
                session,
                url,
                query,
                token=token,
                max_repos=max_repos,
                min_stars=min_stars,
                max_pages=max_pages,
                package_name=package_name,
                cache=cache,
                on_page=progress.on_page,
                on_retry=progress.on_retry,
            )

    try:
        return await _run_with_full_teardown(work())
    except ValueError as e:
        raise ToolError(str(e)) from e
