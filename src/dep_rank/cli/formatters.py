"""Rich-based output formatters for dep-rank CLI."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from rich.console import Console
from rich.table import Table
from rich.text import Text

from dep_rank.core.models import (
    CautionCode,
    CodeSearchResult,
    DependentsResult,
    RetryStatus,
    ScrapeReason,
    ScrapeSnapshot,
)

console = Console()


def humanize(num: int) -> str:
    """Convert large numbers to human-readable format: 1500 → '1.5K'."""
    if num < 1000:
        return str(num)
    if num < 10000:
        return f"{num / 1000:.1f}K"
    if num < 1000000:
        return f"{num // 1000}K"
    if num < 10000000:
        return f"{num / 1000000:.1f}M"
    return f"{num // 1000000}M"


def print_dependents_table(result: DependentsResult) -> None:
    """Print a Rich table of dependents, dispatching on the ranking actually applied."""
    _print_dependents_table(result)

    if result.trust_check is not None:
        check = result.trust_check
        n_concentrated = sum(
            1
            for repo in result.repos
            if repo.trust
            and any(c.code == CautionCode.CONCENTRATED_STARRING for c in repo.trust.cautions)
        )
        console.print(
            f"Trust check (last {check.window_weeks} weeks of star history): "
            f"{check.repos_checked} of {len(result.repos)} repos checked · "
            f"{n_concentrated} concentrated · "
            f"{len(check.insufficient_history)} insufficient history · "
            f"{len(check.unavailable)} unavailable",
            style="dim",
        )


def _print_dependents_table(result: DependentsResult) -> None:
    is_trust = result.ranked_by == "trust"
    title = f"Top dependents of {result.source}" + (" (by trust)" if is_trust else "")
    table = Table(title=title)
    table.add_column("Repository", style="cyan", no_wrap=True)
    if is_trust:
        table.add_column("Trust", justify="right", style="magenta")
    table.add_column("Stars", justify="right", style="yellow")

    has_cautions = is_trust and any(r.trust and r.trust.cautions for r in result.repos)
    if has_cautions:
        table.add_column("Cautions", style="dark_orange")

    has_descriptions = any(r.description for r in result.repos)
    if has_descriptions:
        table.add_column("Description", style="dim")

    for repo in result.repos:
        row = [f"{repo.owner}/{repo.name}"]
        if is_trust:
            score = round(repo.trust.score) if repo.trust else 0
            row.append(str(score))
        row.append(humanize(repo.stars))
        if has_cautions:
            codes = [c.code for c in repo.trust.cautions] if repo.trust else []
            row.append(", ".join(codes))
        if has_descriptions:
            row.append(repo.description or "")
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]{result.total_count:,} dependents at or above the star threshold[/dim]")
    if has_cautions:
        console.print(
            "[dim]Cautions are informational heuristics, "
            "not evidence of fake stars or malicious behavior.[/dim]"
        )


def print_dependents_json(result: DependentsResult, *, include_rank_metadata: bool = False) -> None:
    """Print DependentsResult as JSON.

    Star mode (``include_rank_metadata=False``) excludes ``ranked_by``, ``trust_check``,
    and per-repo ``trust`` so CLI output stays byte-identical to pre-trust-ranking
    releases. Trust mode includes ``trust_check`` only when a check was run.
    ``trust_signals`` is excluded structurally by the model field.
    """
    if include_rank_metadata:
        payload = result.model_dump_json(
            indent=2, exclude={"trust_check"} if result.trust_check is None else None
        )
    else:
        payload = result.model_dump_json(
            indent=2,
            exclude={"ranked_by": True, "trust_check": True, "repos": {"__all__": {"trust"}}},
        )
    console.print(payload, highlight=False)


def print_search_results(result: CodeSearchResult) -> None:
    """Print code search results."""
    if not result.hits:
        console.print(f"[dim]No results found for '{result.query}'[/dim]")
        return

    table = Table(title=f"Code search: '{result.query}'")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("File", style="green")
    table.add_column("Matches", justify="right", style="yellow")

    for hit in result.hits:
        table.add_row(
            f"{hit.repo.owner}/{hit.repo.name}",
            hit.file_path,
            str(hit.matches),
        )

    console.print(table)
    console.print(f"\n[dim]Searched {result.searched_repos} repositories[/dim]")


def partial_warning(reason: ScrapeReason | None) -> str:
    """Render a one-line caveat explaining why a result is partial."""
    messages = {
        ScrapeReason.MAX_PAGES_REACHED: (
            "Stopped at the page cap — results are a partial top-K. "
            "Raise --max-pages (ceiling 1000) for deeper coverage."
        ),
        ScrapeReason.TREND_CONVERGED: (
            "Stopped early — the adaptive heuristic judged the top-K stable. "
            "Use --no-adaptive-stop to scrape until exhaustion or the --max-pages cap."
        ),
        ScrapeReason.NETWORK_FAILURE: ("Scrape ended on a network error — results are partial."),
        ScrapeReason.RATE_LIMITED: (
            "Scrape ended on rate limiting — results are partial. A GitHub token raises the limit."
        ),
    }
    text = messages.get(reason, "Results are partial.") if reason else "Results are partial."
    return f"[yellow]⚠ {text}[/yellow]"


def stale_cache_notice(stale_pages: int, pages_scraped: int) -> str:
    """Render the caveat for an unauthenticated run that served expired cache pages."""
    return (
        f"[yellow]⚠ {stale_pages} of {pages_scraped} pages came from expired cache entries, "
        "which are only refreshed on authenticated runs. Set a GitHub token (--token or "
        'DEP_RANK_TOKEN) or run "dep-rank cache clear" for current results.[/yellow]'
    )


def build_topk_table(snapshot: ScrapeSnapshot) -> Table:
    """Render the running top-K as a Rich table for the Live display during a scrape.

    The title always carries progress context (page + matched count) so that an empty
    top-K — high ``--min-stars``, ``rows=0``, or a genuinely no-match scrape — still
    renders live progress instead of a blank frame. An empty top-K shows a placeholder
    row rather than an empty table.
    """
    table = Table(
        title=(
            f"Top dependents so far "
            f"(page {snapshot.pages_scraped}, {snapshot.matched_count} matched)"
        )
    )
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Stars", justify="right", style="yellow")
    if snapshot.top_k:
        for repo in snapshot.top_k:
            table.add_row(f"{repo.owner}/{repo.name}", humanize(repo.stars))
    else:
        table.add_row("(no matching repositories yet)", "—")
    return table


class RetryCountdown:
    """Rate-limit status line whose remaining wait is recomputed on every render.

    Live and Progress re-render several times a second, so the countdown ticks without
    further updates. ``str()`` feeds Progress text columns; ``__rich__`` feeds Live.
    """

    def __init__(self, status: RetryStatus, *, now: Callable[[], float] = time.monotonic):
        self._status = status
        self._now = now
        self._resume_at = now() + status.delay

    def __str__(self) -> str:
        remaining = max(0, math.ceil(self._resume_at - self._now()))
        minutes, seconds = divmod(remaining, 60)
        s = self._status
        return (
            f"⏳ GitHub rate limit — resuming in {minutes}:{seconds:02d} "
            f"(page {s.page}, retry {s.attempt}/{s.max_retries})"
        )

    def __rich__(self) -> Text:
        return Text(str(self), style="yellow")


def format_scrape_summary(
    pages_scraped: int,
    max_pages: int,
    estimated_total_pages: int,
    found_count: int,
    min_stars: int,
) -> str:
    """Format the scraping completion summary line."""
    pct_max = (pages_scraped / max_pages * 100) if max_pages > 0 else 0.0
    parts = [f"Scraped {pages_scraped}/{max_pages} pages ({pct_max:.1f}%)"]

    if estimated_total_pages > 0:
        pct_est = pages_scraped / estimated_total_pages * 100
        parts.append(f"{pages_scraped}/~{estimated_total_pages:,} estimated pages ({pct_est:.2f}%)")

    parts.append(f"Found {found_count:,} dependents with ≥{min_stars} stars")
    return " · ".join(parts)
