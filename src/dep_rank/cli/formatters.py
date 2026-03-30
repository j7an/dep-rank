"""Rich-based output formatters for dep-rank CLI."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from dep_rank.core.models import CodeSearchResult, DependentsResult

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
    """Print a Rich table of dependents."""
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


def print_dependents_json(result: DependentsResult) -> None:
    """Print DependentsResult as JSON."""
    console.print(result.model_dump_json(indent=2), highlight=False)


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
