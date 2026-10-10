"""Library entry point for the deps pipeline.

Trust scores are pool-relative heuristics, not quality or fraud verdicts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

import httpx2

from dep_rank.core.cache import SqliteCache
from dep_rank.core.graphql import enrich_with_trust_metadata
from dep_rank.core.models import (
    DependentsResult,
    DependentType,
    RetryStatus,
    ScrapeResult,
    ScrapeSnapshot,
    TrustCheckResult,
)
from dep_rank.core.scraper import DEFAULT_MAX_PAGES, scrape_dependents
from dep_rank.core.star_history import check_star_history, skipped_trust_check
from dep_rank.core.trust import compute_trust_scores


async def get_dependents(
    session: httpx2.AsyncClient,
    url: str,
    *,
    rows: int = 10,
    min_stars: int = 5,
    dependent_type: DependentType = DependentType.REPOSITORY,
    token: str | None = None,
    descriptions: bool = False,
    rank_by: Literal["stars", "trust"] = "stars",
    trust_check: bool = False,
    max_pages: int = DEFAULT_MAX_PAGES,
    adaptive_stop: bool = True,
    cache: SqliteCache | None = None,
    on_page: Callable[[ScrapeSnapshot], Awaitable[None]] | None = None,
    on_scraped: Callable[[ScrapeResult], Awaitable[None]] | None = None,
    on_retry: Callable[[RetryStatus], Awaitable[None]] | None = None,
) -> DependentsResult:
    """Scrape and optionally enrich dependents using caller-owned session and cache.

    Trust scores are pool-relative heuristics, not quality or fraud verdicts.
    Raises ValueError for invalid option combinations or URL. The caller owns
    ``session`` and ``cache``; neither is closed here. ``on_scraped`` is awaited
    once immediately after scraping, before enrichment; its exceptions propagate.
    ``on_retry`` is passed to ``scrape_dependents``.
    """
    if rank_by not in ("stars", "trust"):
        msg = f"rank_by must be 'stars' or 'trust', got {rank_by!r}"
        raise ValueError(msg)
    if descriptions and not token:
        msg = "descriptions requires a GitHub token"
        raise ValueError(msg)
    if trust_check and rank_by != "trust":
        msg = "trust_check requires rank_by='trust'"
        raise ValueError(msg)
    if rank_by == "trust" and not token:
        msg = "rank_by='trust' requires a GitHub token"
        raise ValueError(msg)

    scrape_rows = rows
    if rank_by == "trust":
        scrape_rows = 0 if rows <= 0 else max(rows, min(100, rows * 10))

    scrape_result = await scrape_dependents(
        session,
        url,
        dependent_type=dependent_type,
        min_stars=min_stars,
        cache=cache,
        token=token,
        max_pages=max_pages,
        rows=scrape_rows,
        adaptive_stop=adaptive_stop,
        on_page=on_page,
        on_retry=on_retry,
    )
    if on_scraped is not None:
        await on_scraped(scrape_result)

    repos = scrape_result.repos
    total_count = scrape_result.matched_count
    ranked_by: Literal["stars", "trust"] = "stars"
    trust_check_result: TrustCheckResult | None = None
    trust_metadata_complete = True
    # One clock read: the output's scraped_at is also the reference time for
    # age-based caution signals.
    now = datetime.now(tz=UTC)
    # Token is guaranteed by the precondition check; ``and token`` only narrows
    # the type for mypy.
    if rank_by == "trust" and token:
        meta = await enrich_with_trust_metadata(
            session,
            repos,
            token,
            include_description=descriptions,
        )
        if meta.failed:
            repos = sorted(meta.repos, key=lambda r: r.stars, reverse=True)[:rows]
            if trust_check:
                trust_check_result = skipped_trust_check(repos)
        else:
            trust_metadata_complete = meta.complete
            repos = compute_trust_scores(meta.repos, now=now)[:rows]
            ranked_by = "trust"
            if trust_check:
                repos, trust_check_result = await check_star_history(session, repos, token, now=now)
    else:
        repos = repos[:rows]
        if descriptions and token and repos:
            meta = await enrich_with_trust_metadata(session, repos, token, include_description=True)
            repos = sorted(meta.repos, key=lambda r: r.stars, reverse=True)[:rows]

    return DependentsResult(
        source=url,
        total_count=total_count,
        filtered_count=total_count,
        repos=repos,
        dependent_type=dependent_type,
        scraped_at=now,
        complete=scrape_result.complete,
        reason=scrape_result.reason,
        pages_scraped=scrape_result.pages_scraped,
        estimated_total_pages=scrape_result.estimated_total_pages,
        ranked_by=ranked_by,
        trust_check=trust_check_result,
        stale_pages=scrape_result.stale_pages,
        trust_metadata_complete=trust_metadata_complete,
    )
