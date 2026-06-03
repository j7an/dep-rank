"""CLI entry point for dep-rank."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from typing import Literal

import appdirs
import click
from rich.logging import RichHandler

from dep_rank import __version__
from dep_rank.core.models import DependentsResult, DependentType
from dep_rank.core.validation import validate_github_url

logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(show_time=False, show_path=False, markup=True)],
)


async def run_deps(
    url: str,
    rows: int,
    min_stars: int,
    descriptions: bool,
    packages: bool,
    token: str | None,
    verbose: bool = False,
    max_pages: int = 200,
    concurrency: int = 3,
    adaptive_stop: bool = True,
    quiet: bool = False,
    rank_by: str = "stars",
) -> DependentsResult:
    """Run the deps pipeline: scrape → enrich → return."""
    import aiohttp
    from rich.console import Console
    from rich.live import Live

    from dep_rank.cli.formatters import build_topk_table, format_scrape_summary
    from dep_rank.core.cache import SqliteCache
    from dep_rank.core.graphql import enrich_with_graphql
    from dep_rank.core.models import ScrapeSnapshot
    from dep_rank.core.scraper import scrape_dependents

    console = Console(stderr=True)
    cache_dir = appdirs.user_cache_dir("dep-rank")
    cache = SqliteCache(cache_dir)
    await cache.initialize()

    dep_type = DependentType.PACKAGE if packages else DependentType.REPOSITORY

    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "dep-rank/0.1"},
        ) as session:
            show_live = not verbose and not quiet
            live = (
                Live(console=console, refresh_per_second=4, transient=True) if show_live else None
            )

            async def on_partial(snapshot: ScrapeSnapshot) -> None:
                # Update on EVERY snapshot, even when top_k is empty: high --min-stars,
                # rows=0, or a no-match scrape must still show live progress.
                # build_topk_table renders page/matched/empty-state for the empty case.
                if live is not None:
                    live.update(build_topk_table(snapshot))

            scrape_rows = rows
            if rank_by == "trust":
                scrape_rows = 0 if rows <= 0 else max(rows, min(100, rows * 10))

            if live is not None:
                live.start()
            try:
                scrape_result = await scrape_dependents(
                    session,
                    url,
                    dependent_type=dep_type,
                    min_stars=min_stars,
                    cache=cache,
                    token=token,
                    max_pages=max_pages,
                    rows=scrape_rows,
                    concurrency=concurrency,
                    adaptive_stop=adaptive_stop,
                    on_partial=on_partial,
                )
            finally:
                if live is not None:
                    live.stop()

            repos = scrape_result.repos
            total_count = scrape_result.matched_count

            if not quiet:
                summary = format_scrape_summary(
                    pages_scraped=scrape_result.pages_scraped,
                    max_pages=scrape_result.max_pages,
                    estimated_total_pages=scrape_result.estimated_total_pages,
                    found_count=total_count,
                    min_stars=min_stars,
                )
                console.print(f"[green]{summary}")
                if not scrape_result.complete:
                    from dep_rank.cli.formatters import partial_warning

                    console.print(partial_warning(scrape_result.reason))

            ranked_by: Literal["stars", "trust"] = "stars"
            # ``and token`` makes the token precondition explicit (the CLI preflight
            # already enforces it) and narrows the type for the enrich call. A direct
            # caller passing rank_by="trust" without a token degrades to star ranking.
            if rank_by == "trust" and token:
                from dep_rank.core.graphql import enrich_with_trust_metadata
                from dep_rank.core.trust import compute_trust_scores

                meta = await enrich_with_trust_metadata(
                    session,
                    repos,
                    token,
                    include_description=descriptions,
                    cache=cache,
                )
                if meta.failed:
                    if not quiet:
                        console.print(
                            "[yellow]⚠ Trust metadata fetch failed — "
                            "falling back to star ranking.[/yellow]"
                        )
                    repos = sorted(meta.repos, key=lambda r: r.stars, reverse=True)[:rows]
                else:
                    if not meta.complete and not quiet:
                        console.print(
                            "[yellow]⚠ Some trust metadata was missing — "
                            "scores use partial data.[/yellow]"
                        )
                    repos = compute_trust_scores(meta.repos)[:rows]
                    ranked_by = "trust"
            else:
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
                complete=scrape_result.complete,
                reason=scrape_result.reason,
                pages_scraped=scrape_result.pages_scraped,
                estimated_total_pages=scrape_result.estimated_total_pages,
                ranked_by=ranked_by,
            )
    finally:
        await cache.close()


@click.group()
@click.version_option(version=__version__, prog_name="dep-rank")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose/debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Analyze GitHub repository dependents by star count."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    if verbose:
        logging.getLogger("dep_rank").setLevel(logging.DEBUG)


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
@click.option(
    "--max-pages", default=200, help="Maximum pages to scrape (default: 200, ceiling 1000)."
)
@click.option(
    "--concurrency",
    type=click.IntRange(1, 10),
    default=3,
    help="Max concurrent page fetches (1-10, default: 3).",
)
@click.option(
    "--adaptive-stop/--no-adaptive-stop",
    default=True,
    help="Stop early when recent pages can no longer change the top-K (default: on).",
)
@click.option(
    "--rank-by",
    type=click.Choice(["stars", "trust"]),
    default="stars",
    help="Ranking strategy: stars (default) or trust (heuristic, requires token).",
)
@click.pass_context
def deps(
    ctx: click.Context,
    url: str,
    rows: int,
    min_stars: int,
    output_format: str,
    descriptions: bool,
    packages: bool,
    token: str | None,
    max_pages: int,
    concurrency: int,
    adaptive_stop: bool,
    rank_by: str,
) -> None:
    """List top dependents of a GitHub repository, ranked by stars (default) or trust."""
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

    if rank_by == "trust" and not token:
        click.echo(
            "Error: --rank-by trust requires a GitHub token (--token or DEP_RANK_TOKEN env var)",
            err=True,
        )
        sys.exit(1)

    if max_pages > 1000:
        click.echo("Warning: --max-pages capped at the 1000 ceiling.", err=True)
        max_pages = 1000

    if not token:
        click.echo(
            "Warning: no GitHub token configured (--token or DEP_RANK_TOKEN). "
            "Unauthenticated scraping is limited to ~60 requests/hour; "
            "large repositories will be slow or return partial results.",
            err=True,
        )

    verbose = ctx.obj.get("verbose", False)
    result = asyncio.run(
        run_deps(
            url,
            rows,
            min_stars,
            descriptions,
            packages,
            token,
            verbose,
            max_pages=max_pages,
            concurrency=concurrency,
            adaptive_stop=adaptive_stop,
            quiet=(output_format == "json"),
            rank_by=rank_by,
        )
    )

    from dep_rank.cli.formatters import print_dependents_json, print_dependents_table

    if output_format == "json":
        print_dependents_json(result, include_rank_metadata=(rank_by == "trust"))
    else:
        print_dependents_table(result)


@cli.command()
@click.argument("url")
@click.argument("query")
@click.option("--max-repos", default=10, help="Maximum repos to search.")
@click.option("--min-stars", default=50, help="Only search repos with this many stars.")
@click.option("--token", envvar="DEP_RANK_TOKEN", required=True, help="GitHub token (required).")
@click.option(
    "--max-pages",
    default=200,
    help="Maximum pages to scrape (default: 200, ceiling 1000).",
)
@click.option(
    "--concurrency",
    type=click.IntRange(1, 10),
    default=3,
    help="Max concurrent page fetches (1-10, default: 3).",
)
@click.pass_context
def search(
    ctx: click.Context,
    url: str,
    query: str,
    max_repos: int,
    min_stars: int,
    token: str,
    max_pages: int,
    concurrency: int,
) -> None:
    """Search code patterns across dependents of a GitHub repository."""
    try:
        validate_github_url(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if max_pages > 1000:
        click.echo("Warning: --max-pages capped at the 1000 ceiling.", err=True)
        max_pages = 1000

    verbose = ctx.obj.get("verbose", False)

    async def _run() -> None:
        import aiohttp
        from rich.console import Console

        from dep_rank.cli.formatters import print_search_results
        from dep_rank.core.cache import SqliteCache
        from dep_rank.core.scraper import scrape_dependents
        from dep_rank.core.search import search_code

        console = Console(stderr=True)
        cache_dir = appdirs.user_cache_dir("dep-rank")
        cache = SqliteCache(cache_dir)
        await cache.initialize()

        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": "dep-rank/0.1"},
            ) as session:
                from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

                from dep_rank.cli.formatters import format_scrape_summary

                progress_ctx = None
                task_id = None
                if not verbose:
                    progress_ctx = Progress(
                        TextColumn("[bold green]Scraping dependents..."),
                        BarColumn(),
                        TextColumn(
                            "{task.completed}/{task.total} pages ({task.percentage:>5.1f}%)"
                        ),
                        TextColumn("·"),
                        TextColumn("{task.fields[est_text]}"),
                        TimeElapsedColumn(),
                        console=console,
                    )
                    task_id = progress_ctx.add_task(
                        "scraping", total=max_pages, est_text="estimating..."
                    )

                async def on_progress(page: int, est_total: int) -> None:
                    if progress_ctx is not None and task_id is not None:
                        est_text = (
                            f"{page}/~{est_total:,} estimated pages ({page / est_total * 100:.2f}%)"
                            if est_total > 0
                            else "estimating..."
                        )
                        progress_ctx.update(task_id, completed=page, est_text=est_text)

                if progress_ctx is not None:
                    progress_ctx.start()
                try:
                    scrape_result = await scrape_dependents(
                        session,
                        url,
                        min_stars=min_stars,
                        cache=cache,
                        on_progress=on_progress,
                        token=token,
                        max_pages=max_pages,
                        rows=max_repos,
                        concurrency=concurrency,
                        adaptive_stop=False,
                    )
                finally:
                    if progress_ctx is not None:
                        progress_ctx.stop()

                repos = scrape_result.repos
                summary = format_scrape_summary(
                    pages_scraped=scrape_result.pages_scraped,
                    max_pages=scrape_result.max_pages,
                    estimated_total_pages=scrape_result.estimated_total_pages,
                    found_count=scrape_result.matched_count,
                    min_stars=min_stars,
                )
                console.print(f"[green]{summary}")
                if not scrape_result.complete:
                    from dep_rank.cli.formatters import partial_warning

                    console.print(partial_warning(scrape_result.reason))

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
