"""FastMCP tool surface for dep-rank; a thin layer over the core pipelines."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Literal

import httpx2
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from dep_rank import __version__
from dep_rank.core.cache import open_cache
from dep_rank.core.dependents import get_dependents
from dep_rank.core.models import CodeSearchResult, DependentsResult, DependentType
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

mcp = FastMCP("dep-rank", instructions=INSTRUCTIONS)

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


def _http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers={"User-Agent": f"dep-rank/{__version__}"},
        trust_env=False,
    )


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
) -> DependentsResult:
    """List a GitHub repository's dependents, ranked by stars or a pool-relative trust score.

    `url` is a github.com repository URL or `owner/repo`. `rows` caps the returned
    repositories. When `complete` is false, counts are lower bounds and `reason` says
    why the scrape stopped. Trust scores are relative to the scraped pool, not verdicts.
    """
    token = _check_call(url)
    try:
        async with _lock(), open_cache() as cache, _http_client() as session:
            result = await get_dependents(
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
                cache=cache,
            )
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
    max_repos: Annotated[int, Field(ge=1)] = 10,
    min_stars: Annotated[int, Field(ge=0)] = 50,
    max_pages: Annotated[int, Field(ge=1, le=1000)] = DEFAULT_MAX_PAGES,
) -> CodeSearchResult:
    """Search code across the top-starred dependents of a GitHub repository.

    `url` is a github.com repository URL or `owner/repo`. The top `max_repos` dependents
    with at least `min_stars` stars are searched with GitHub code search. When `complete`
    is false, the dependent scrape stopped early and `reason` says why.
    """
    token = _check_call(url)
    try:
        async with _lock(), open_cache() as cache, _http_client() as session:
            return await search_dependents(
                session,
                url,
                query,
                token=token,
                max_repos=max_repos,
                min_stars=min_stars,
                max_pages=max_pages,
                cache=cache,
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
