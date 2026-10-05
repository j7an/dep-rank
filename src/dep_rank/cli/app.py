"""CLI entry point for dep-rank."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import sys
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Literal, cast

import click
from rich.console import Console
from rich.logging import RichHandler

from dep_rank import __version__
from dep_rank.core.models import DependentsResult, DependentType
from dep_rank.core.validation import validate_github_url

if TYPE_CHECKING:
    from dep_rank.core.cache import SqliteCache
    from dep_rank.core.models import ScrapeResult

_stderr_console = Console(stderr=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(console=_stderr_console, show_time=False, show_path=False, markup=True)],
)


def _win_local_appdata() -> str:  # pragma: no cover
    """Resolve the Windows shell's local application data folder."""
    if sys.platform != "win32":
        raise OSError("Windows only")
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buf)
    return buf.value


def _cache_dir() -> str:
    """Resolve the platform cache directory using current environment settings."""
    if sys.platform == "win32":
        return os.path.join(_win_local_appdata(), "dep-rank", "dep-rank", "Cache")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", "dep-rank")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "dep-rank")


@contextlib.asynccontextmanager
async def _open_cache() -> AsyncIterator[SqliteCache]:
    """Initialize a cache and close it when its caller leaves the context."""
    from dep_rank.core.cache import SqliteCache

    cache = SqliteCache(_cache_dir())
    await cache.initialize()
    try:
        yield cache
    finally:
        await cache.close()


def _validate_url_or_exit(url: str) -> None:
    """Validate a repository URL and report invalid input to stderr."""
    try:
        validate_github_url(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _cap_max_pages(max_pages: int) -> int:
    """Clamp the page budget to its ceiling and warn when it exceeds it."""
    if max_pages > 1000:
        click.echo("Warning: --max-pages capped at the 1000 ceiling.", err=True)
        return 1000
    return max_pages


def _print_scrape_outcome(console: Console, scrape_result: ScrapeResult, min_stars: int) -> None:
    """Print the scrape summary and any partial-result warning."""
    from dep_rank.cli.formatters import format_scrape_summary, partial_warning

    summary = format_scrape_summary(
        pages_scraped=scrape_result.pages_scraped,
        max_pages=scrape_result.max_pages,
        estimated_total_pages=scrape_result.estimated_total_pages,
        found_count=scrape_result.matched_count,
        min_stars=min_stars,
    )
    console.print(f"[green]{summary}")
    if not scrape_result.complete:
        console.print(partial_warning(scrape_result.reason))


async def run_deps(
    url: str,
    rows: int,
    min_stars: int,
    descriptions: bool,
    packages: bool,
    token: str | None,
    verbose: bool = False,
    max_pages: int = 200,
    adaptive_stop: bool = True,
    quiet: bool = False,
    rank_by: str = "stars",
    trust_check: bool = False,
) -> DependentsResult:
    """Run the deps pipeline: scrape → enrich → return."""
    import aiohttp
    from rich.live import Live

    from dep_rank.cli.formatters import build_topk_table
    from dep_rank.core.dependents import get_dependents
    from dep_rank.core.models import ScrapeSnapshot

    console = _stderr_console
    async with _open_cache() as cache:
        dep_type = DependentType.PACKAGE if packages else DependentType.REPOSITORY
        async with aiohttp.ClientSession(
            headers={"User-Agent": "dep-rank/0.1"},
        ) as session:
            show_live = not verbose and not quiet
            live = (
                Live(console=console, refresh_per_second=4, transient=True) if show_live else None
            )

            async def on_page(snapshot: ScrapeSnapshot) -> None:
                # Update on EVERY snapshot, even when top_k is empty: high --min-stars,
                # rows=0, or a no-match scrape must still show live progress.
                # build_topk_table renders page/matched/empty-state for the empty case.
                if live is not None:
                    live.update(build_topk_table(snapshot))

            async def on_scraped(scrape_result: ScrapeResult) -> None:
                nonlocal live
                if live is not None:
                    live.stop()
                    live = None
                if not quiet:
                    _print_scrape_outcome(console, scrape_result, min_stars)
                if scrape_result.stale_pages and not token:
                    # Without a token nothing refreshes expired pages, so say so even in JSON
                    # mode (stderr only; stdout stays parseable).
                    from dep_rank.cli.formatters import stale_cache_notice

                    console.print(
                        stale_cache_notice(scrape_result.stale_pages, scrape_result.pages_scraped)
                    )

            if live is not None:
                live.start()
            try:
                result = await get_dependents(
                    session,
                    url,
                    rows=rows,
                    min_stars=min_stars,
                    dependent_type=dep_type,
                    token=token,
                    descriptions=descriptions,
                    rank_by=cast(Literal["stars", "trust"], rank_by),
                    trust_check=trust_check,
                    max_pages=max_pages,
                    adaptive_stop=adaptive_stop,
                    cache=cache,
                    on_page=on_page,
                    on_scraped=on_scraped,
                )
            finally:
                if live is not None:
                    live.stop()

            if rank_by == "trust" and result.ranked_by == "stars":
                if not quiet:
                    console.print(
                        "[yellow]⚠ Trust metadata fetch failed — "
                        "falling back to star ranking.[/yellow]"
                    )
                    if trust_check:
                        console.print(
                            "[yellow]⚠ Trust check skipped — trust ranking unavailable.[/yellow]"
                        )
            elif result.ranked_by == "trust" and not quiet:
                if not result.trust_metadata_complete:
                    console.print(
                        "[yellow]⚠ Some trust metadata was missing — "
                        "scores use partial data.[/yellow]"
                    )
                if result.trust_check and not result.trust_check.complete:
                    console.print(
                        "[yellow]⚠ Trust check incomplete — star history unavailable for "
                        f"{len(result.trust_check.unavailable)} repos.[/yellow]"
                    )

            return result


@click.group()
@click.version_option(version=__version__, prog_name="dep-rank")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose/debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Analyze GitHub repository dependents, ranked by stars or trust."""
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
    type=int,
    hidden=True,
    expose_value=False,
    deprecated="has no effect; dependents pages are fetched serially",
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
@click.option(
    "--trust-check",
    is_flag=True,
    default=False,
    help="Sample recent star history of the top trust-ranked results for concentrated starring "
    "(heuristic; requires --rank-by trust).",
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
    adaptive_stop: bool,
    rank_by: str,
    trust_check: bool,
) -> None:
    """List top dependents of a GitHub repository, ranked by stars (default) or trust."""
    _validate_url_or_exit(url)

    if descriptions and not token:
        click.echo(
            "Error: --descriptions requires a GitHub token (--token or DEP_RANK_TOKEN env var)",
            err=True,
        )
        sys.exit(1)

    if trust_check and rank_by != "trust":
        click.echo("Error: --trust-check requires --rank-by trust", err=True)
        sys.exit(1)

    if rank_by == "trust" and not token:
        click.echo(
            "Error: --rank-by trust requires a GitHub token (--token or DEP_RANK_TOKEN env var)",
            err=True,
        )
        sys.exit(1)

    max_pages = _cap_max_pages(max_pages)

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
            adaptive_stop=adaptive_stop,
            quiet=(output_format == "json"),
            rank_by=rank_by,
            trust_check=trust_check,
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
    type=int,
    hidden=True,
    expose_value=False,
    deprecated="has no effect; dependents pages are fetched serially",
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
) -> None:
    """Search code patterns across dependents of a GitHub repository."""
    _validate_url_or_exit(url)

    max_pages = _cap_max_pages(max_pages)

    verbose = ctx.obj.get("verbose", False)

    async def _run() -> None:
        import aiohttp

        from dep_rank.cli.formatters import print_search_results
        from dep_rank.core.models import ScrapeSnapshot
        from dep_rank.core.scraper import scrape_dependents
        from dep_rank.core.search import search_code

        console = _stderr_console
        async with _open_cache() as cache:
            async with aiohttp.ClientSession(
                headers={"User-Agent": "dep-rank/0.1"},
            ) as session:
                from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

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

                async def on_page(snapshot: ScrapeSnapshot) -> None:
                    page = snapshot.pages_scraped
                    est_total = snapshot.estimated_total_pages
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
                        on_page=on_page,
                        token=token,
                        max_pages=max_pages,
                        rows=max_repos,
                        adaptive_stop=False,
                    )
                finally:
                    if progress_ctx is not None:
                        progress_ctx.stop()

                repos = scrape_result.repos
                _print_scrape_outcome(console, scrape_result, min_stars)

                result = await search_code(
                    session,
                    repos,
                    query,
                    token=token,
                    max_repos=max_repos,
                )
                print_search_results(result)

    asyncio.run(_run())


@cli.group()
def cache() -> None:
    """Manage the HTTP response cache."""


@cache.command()
def clear() -> None:
    """Clear all cached data."""

    async def _clear() -> None:
        async with _open_cache() as cache:
            await cache.clear()
        click.echo("Cache cleared.")

    asyncio.run(_clear())


@cache.command()
def stats() -> None:
    """Show cache statistics."""

    async def _stats() -> None:
        async with _open_cache() as cache:
            s = await cache.stats()
        click.echo(f"Entries: {s['entries']}")
        click.echo(f"Size: {s['size_bytes']:,} bytes")

    asyncio.run(_stats())
