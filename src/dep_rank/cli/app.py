"""CLI entry point for dep-rank."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import appdirs
import click

if TYPE_CHECKING:
    from typing import Literal

from dep_rank import __version__
from dep_rank.core.models import DependentsResult, DependentType
from dep_rank.core.validation import validate_github_url


async def run_deps(
    url: str,
    rows: int,
    min_stars: int,
    descriptions: bool,
    packages: bool,
    token: str | None,
) -> DependentsResult:
    """Run the deps pipeline: scrape → enrich → return."""
    import aiohttp

    from dep_rank.core.cache import SqliteCache
    from dep_rank.core.graphql import enrich_with_graphql
    from dep_rank.core.scraper import scrape_dependents

    cache_dir = appdirs.user_cache_dir("dep-rank")
    cache = SqliteCache(cache_dir)
    await cache.initialize()

    dep_type = DependentType.PACKAGE if packages else DependentType.REPOSITORY

    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "dep-rank/0.1"},
        ) as session:
            repos = await scrape_dependents(
                session,
                url,
                dependent_type=dep_type,
                min_stars=min_stars,
                cache=cache,
            )

            total_count = len(repos)
            repos = repos[:rows]

            if descriptions and token and repos:
                repos = await enrich_with_graphql(session, repos, token, cache=cache)
                repos = repos[:rows]

            return DependentsResult(
                source=url,
                total_count=total_count,
                filtered_count=total_count,
                repos=repos,
                dependent_type=dep_type,
                scraped_at=datetime.now(tz=UTC),
            )
    finally:
        await cache.close()


@click.group()
@click.version_option(version=__version__, prog_name="dep-rank")
def cli() -> None:
    """Analyze GitHub repository dependents by star count."""


@cli.command()
@click.argument("url")
@click.option("--rows", default=10, help="Number of results to display.")
@click.option("--min-stars", default=5, help="Minimum star count filter.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
@click.option(
    "--descriptions/--no-descriptions", default=False, help="Fetch descriptions via GitHub API."
)
@click.option(
    "--packages/--repositories", default=False, help="Search packages instead of repositories."
)
@click.option("--token", envvar="DEP_RANK_TOKEN", default=None, help="GitHub token.")
def deps(
    url: str,
    rows: int,
    min_stars: int,
    output_format: str,
    descriptions: bool,
    packages: bool,
    token: str | None,
) -> None:
    """List top dependents of a GitHub repository, ranked by stars."""
    try:
        validate_github_url(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if descriptions and not token:
        click.echo(
            "Error: --descriptions requires a GitHub token (--token or DEP_RANK_TOKEN env var)",
            err=True,
        )
        sys.exit(1)

    result = asyncio.run(run_deps(url, rows, min_stars, descriptions, packages, token))

    from dep_rank.cli.formatters import print_dependents_json, print_dependents_table

    if output_format == "json":
        print_dependents_json(result)
    else:
        print_dependents_table(result)


@cli.command()
@click.argument("url")
@click.argument("query")
@click.option("--max-repos", default=10, help="Maximum repos to search.")
@click.option("--min-stars", default=50, help="Only search repos with this many stars.")
@click.option("--token", envvar="DEP_RANK_TOKEN", required=True, help="GitHub token (required).")
def search(url: str, query: str, max_repos: int, min_stars: int, token: str) -> None:
    """Search code patterns across dependents of a GitHub repository."""
    try:
        validate_github_url(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    async def _run() -> None:
        import aiohttp

        from dep_rank.cli.formatters import print_search_results
        from dep_rank.core.cache import SqliteCache
        from dep_rank.core.scraper import scrape_dependents
        from dep_rank.core.search import search_code

        cache_dir = appdirs.user_cache_dir("dep-rank")
        cache = SqliteCache(cache_dir)
        await cache.initialize()

        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": "dep-rank/0.1"},
            ) as session:
                repos = await scrape_dependents(
                    session,
                    url,
                    min_stars=min_stars,
                    cache=cache,
                )
                result = await search_code(
                    session,
                    repos,
                    query,
                    token=token,
                    max_repos=max_repos,
                )
                print_search_results(result)
        finally:
            await cache.close()

    asyncio.run(_run())


@cli.command(name="mcp")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    help="MCP transport.",
)
@click.option("--port", default=8000, help="HTTP port (only for http transport).")
@click.option("--host", default="127.0.0.1", help="HTTP host (only for http transport).")
def mcp_cmd(transport: str, port: int, host: str) -> None:
    """Start the MCP server."""
    try:
        from dep_rank.mcp.server import mcp as mcp_server
    except ImportError:
        click.echo(
            "Error: MCP support requires fastmcp. Install with: pip install 'dep-rank[mcp]'",
            err=True,
        )
        sys.exit(1)

    mcp_server.run(transport=cast("Literal['stdio', 'http']", transport), host=host, port=port)


@cli.group()
def cache() -> None:
    """Manage the HTTP response cache."""


@cache.command()
def clear() -> None:
    """Clear all cached data."""

    async def _clear() -> None:
        from dep_rank.core.cache import SqliteCache

        cache_dir = appdirs.user_cache_dir("dep-rank")
        c = SqliteCache(cache_dir)
        await c.initialize()
        await c.clear()
        await c.close()
        click.echo("Cache cleared.")

    asyncio.run(_clear())


@cache.command()
def stats() -> None:
    """Show cache statistics."""

    async def _stats() -> None:
        from dep_rank.core.cache import SqliteCache

        cache_dir = appdirs.user_cache_dir("dep-rank")
        c = SqliteCache(cache_dir)
        await c.initialize()
        s = await c.stats()
        await c.close()
        click.echo(f"Entries: {s['entries']}")
        click.echo(f"Size: {s['size_bytes']:,} bytes")

    asyncio.run(_stats())
