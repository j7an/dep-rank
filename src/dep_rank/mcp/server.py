"""FastMCP v3.1 server for dep-rank."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import aiohttp
import appdirs
from fastmcp import Context, FastMCP

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import (
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
)
from dep_rank.core.scraper import scrape_dependents
from dep_rank.core.search import search_code
from dep_rank.core.validation import validate_github_url


@asynccontextmanager
async def _lifespan(server: FastMCP[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Initialize shared resources."""
    token = os.environ.get("DEP_RANK_TOKEN")
    session = aiohttp.ClientSession(headers={"User-Agent": "dep-rank/0.1"})
    cache_dir = appdirs.user_cache_dir("dep-rank")
    cache = SqliteCache(cache_dir)
    await cache.initialize()

    if not token:
        server.disable(tags={"requires-auth"})

    try:
        yield {"token": token, "session": session, "cache": cache}
    finally:
        await cache.close()
        await session.close()


mcp = FastMCP(name="dep-rank", version="0.1.0", lifespan=_lifespan)


@mcp.tool(
    tags={"github", "dependencies"},
    annotations={
        "title": "Get Top Dependents",
        "readOnlyHint": True,
        "openWorldHint": True,
        "idempotentHint": True,
        "destructiveHint": False,
    },
)
async def get_top_dependents(
    url: str,
    rows: int = 10,
    min_stars: int = 5,
    dependent_type: str = "REPOSITORY",
    ctx: Context = None,
) -> DependentsResult:
    """Get the most-starred repositories that depend on a GitHub repository.

    Args:
        url: GitHub repository URL (e.g., https://github.com/django/django)
        rows: Number of results to return (default: 10)
        min_stars: Minimum star count filter (default: 5)
        dependent_type: REPOSITORY or PACKAGE (default: REPOSITORY)
    """
    validate_github_url(url)
    state = ctx.lifespan_context
    dep_type = DependentType(dependent_type)

    async def on_progress(current: int, total: int) -> None:
        if total > 0:
            await ctx.report_progress(progress=current, total=total)

    await ctx.info(f"Scraping dependents for {url}")

    repos = await scrape_dependents(
        state["session"],
        url,
        dependent_type=dep_type,
        min_stars=min_stars,
        cache=state["cache"],
        on_progress=on_progress,
    )

    total_count = len(repos)
    repos = repos[:rows]

    await ctx.set_state(f"deps:{url}", [r.model_dump() for r in repos])

    return DependentsResult(
        source=url,
        total_count=total_count,
        filtered_count=total_count,
        repos=repos,
        dependent_type=dep_type,
        scraped_at=datetime.now(tz=UTC),
    )


@mcp.tool(
    tags={"github", "dependencies", "requires-auth"},
    annotations={
        "title": "Get Dependent Details",
        "readOnlyHint": True,
        "openWorldHint": True,
        "idempotentHint": True,
        "destructiveHint": False,
    },
)
async def get_dependent_details(
    url: str,
    rows: int = 10,
    ctx: Context = None,
) -> DependentsResult:
    """Enrich dependents with accurate star counts and descriptions via GitHub GraphQL API.

    Call get_top_dependents first, then call this to enrich the results.

    Args:
        url: GitHub repository URL (same as used in get_top_dependents)
        rows: Number of results to enrich (default: 10)
    """
    from dep_rank.core.graphql import enrich_with_graphql

    validate_github_url(url)
    state = ctx.lifespan_context
    token = state.get("token")

    if not token:
        msg = "DEP_RANK_TOKEN environment variable is required"
        raise Exception(msg)  # noqa: TRY002

    cached = await ctx.get_state(f"deps:{url}")
    if cached:
        repos = [Repository.model_validate(r) for r in cached]
    else:
        await ctx.info(f"No cached results — scraping dependents for {url}")
        repos = await scrape_dependents(state["session"], url, cache=state["cache"])

    repos = repos[:rows]

    await ctx.info(f"Enriching {len(repos)} repos via GraphQL")
    enriched = await enrich_with_graphql(state["session"], repos, token, cache=state["cache"])
    enriched = enriched[:rows]

    return DependentsResult(
        source=url,
        total_count=len(enriched),
        filtered_count=len(enriched),
        repos=enriched,
        dependent_type=DependentType.REPOSITORY,
        scraped_at=datetime.now(tz=UTC),
    )


@mcp.tool(
    tags={"github", "search", "requires-auth"},
    annotations={
        "title": "Search Dependent Code",
        "readOnlyHint": True,
        "openWorldHint": True,
        "idempotentHint": True,
        "destructiveHint": False,
    },
)
async def search_dependent_code(
    url: str,
    query: str,
    max_repos: int = 10,
    min_stars: int = 50,
    ctx: Context = None,
) -> CodeSearchResult:
    """Search for code patterns across the top dependents of a GitHub repository.

    Args:
        url: GitHub repository URL
        query: Code search query string
        max_repos: Maximum number of repos to search (default: 10)
        min_stars: Only search repos with at least this many stars (default: 50)
    """
    validate_github_url(url)
    state = ctx.lifespan_context
    token = state.get("token")

    if not token:
        msg = "DEP_RANK_TOKEN environment variable is required"
        raise Exception(msg)  # noqa: TRY002

    cached = await ctx.get_state(f"deps:{url}")
    if cached:
        repos = [Repository.model_validate(r) for r in cached]
    else:
        await ctx.info(f"Scraping dependents for {url}")
        repos = await scrape_dependents(
            state["session"],
            url,
            min_stars=min_stars,
            cache=state["cache"],
        )

    async def on_progress(current: int, total: int) -> None:
        await ctx.report_progress(progress=current, total=total)

    await ctx.info(f"Searching '{query}' across top {max_repos} dependents")
    return await search_code(
        state["session"],
        repos,
        query,
        token=token,
        max_repos=max_repos,
        on_progress=on_progress,
    )


@mcp.prompt(tags={"analysis"})
def analyze_ecosystem(repo_url: str) -> str:
    """Comprehensive dependency ecosystem analysis."""
    return (
        f"Analyze the dependency ecosystem for {repo_url}:\n"
        "1. Use get_top_dependents to find the most-starred dependents (rows=20, min_stars=50)\n"
        "2. Use get_dependent_details to get descriptions for the top results\n"
        "3. Summarize: who depends on this repo, what categories of projects, and any notable users"
    )


@mcp.prompt(tags={"search"})
def find_usage_patterns(repo_url: str, pattern: str) -> str:
    """Find how dependents use a specific API or pattern."""
    return (
        f"Find how popular projects use '{pattern}' from {repo_url}:\n"
        "1. Use get_top_dependents with min_stars=100 to find significant dependents\n"
        f"2. Use search_dependent_code with query='{pattern}' to find usage examples\n"
        "3. Summarize the common usage patterns you find"
    )


def main() -> None:
    """Entry point for dep-rank-mcp console script."""
    mcp.run()
