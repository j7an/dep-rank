"""Rich-based output formatters for dep-rank CLI."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from dep_rank.core.models import CodeSearchResult, DependentsResult, ScrapeReason, ScrapeSnapshot

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
    if result.ranked_by == "trust":
        _print_trust_table(result)
    else:
        _print_star_table(result)


def _print_star_table(result: DependentsResult) -> None:
    table = Table(title=f"Top dependents of {result.source}")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Stars", justify="right", style="yellow")

    has_descriptions = any(r.description for r in result.repos)
    if has_descriptions:
        table.add_column("Description", style="dim")

    for repo in result.repos:
        row = [f"{repo.owner}/{repo.name}", humanize(repo.stars)]
        if has_descriptions:
            row.append(repo.description or "")
        table.add_row(*row)

    console.print(table)
    console.print(
        f"\n[dim]{result.total_count:,} total dependents, "
        f"{result.filtered_count:,} with stars above threshold[/dim]"
    )


def _print_trust_table(result: DependentsResult) -> None:
    table = Table(title=f"Top dependents of {result.source} (by trust)")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Trust", justify="right", style="magenta")
    table.add_column("Stars", justify="right", style="yellow")

    has_descriptions = any(r.description for r in result.repos)
    if has_descriptions:
        table.add_column("Description", style="dim")

    for repo in result.repos:
        score = round(repo.trust.score) if repo.trust else 0
        row = [f"{repo.owner}/{repo.name}", str(score), humanize(repo.stars)]
        if has_descriptions:
            row.append(repo.description or "")
        table.add_row(*row)

    console.print(table)
    console.print(
        f"\n[dim]{result.total_count:,} total dependents, "
        f"{result.filtered_count:,} with stars above threshold[/dim]"
    )


def print_dependents_json(result: DependentsResult, *, include_rank_metadata: bool = False) -> None:
    """Print DependentsResult as JSON.

    Star mode (``include_rank_metadata=False``) excludes ``ranked_by`` and per-repo
    ``trust`` so CLI output stays byte-identical to pre-trust-ranking releases. Trust
    mode includes both. ``trust_signals`` is excluded structurally by the model field.
    """
    if include_rank_metadata:
        payload = result.model_dump_json(indent=2)
    else:
        payload = result.model_dump_json(
            indent=2,
            exclude={"ranked_by": True, "repos": {"__all__": {"trust"}}},
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
