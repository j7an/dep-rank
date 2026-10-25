"""GitHub code search across dependent repositories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx2

from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    Repository,
    RetryStatus,
    ScrapeResult,
    ScrapeSnapshot,
)
from dep_rank.core.rate_limiter import RateLimiter
from dep_rank.core.scraper import DEFAULT_MAX_PAGES, REQUEST_TIMEOUT, scrape_dependents

if TYPE_CHECKING:
    from dep_rank.core.cache import SqliteCache

SEARCH_URL = "https://api.github.com/search/code"
# GitHub code search: 10 requests/minute authenticated
SEARCH_RATE_LIMITER = RateLimiter(rate=10, period=60.0)


async def search_code(
    session: httpx2.AsyncClient,
    repos: list[Repository],
    query: str,
    token: str,
    max_repos: int = 10,
) -> CodeSearchResult:
    """Search for code patterns across dependent repositories.

    Args:
        session: httpx2 client.
        repos: List of repositories to search (searched in order, up to max_repos).
        query: Code search query string.
        token: GitHub token (required for code search).
        max_repos: Maximum number of repos to search.
    """
    if not repos:
        return CodeSearchResult(source="", query=query, hits=[], searched_repos=0)

    hits: list[CodeSearchHit] = []
    search_repos = repos[:max_repos]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.text-match+json",
    }

    for repo in search_repos:
        await SEARCH_RATE_LIMITER.acquire()

        search_query = f"{query} repo:{repo.owner}/{repo.name}"
        url = f"{SEARCH_URL}?q={quote(search_query, safe='')}"

        try:
            resp = await session.get(
                url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
        except (httpx2.RequestError, ValueError):
            continue

        for item in data.get("items", []):
            text_matches = item.get("text_matches", [])
            hits.append(
                CodeSearchHit(
                    repo=repo,
                    file_url=item.get("html_url", ""),
                    file_path=item.get("path", ""),
                    matches=len(text_matches),
                )
            )

    return CodeSearchResult(
        source=repos[0].url,
        query=query,
        hits=hits,
        searched_repos=len(search_repos),
    )


async def search_dependents(
    session: httpx2.AsyncClient,
    url: str,
    query: str,
    *,
    token: str,
    max_repos: int = 10,
    min_stars: int = 50,
    max_pages: int = DEFAULT_MAX_PAGES,
    cache: SqliteCache | None = None,
    on_page: Callable[[ScrapeSnapshot], Awaitable[None]] | None = None,
    on_scraped: Callable[[ScrapeResult], Awaitable[None]] | None = None,
    on_retry: Callable[[RetryStatus], Awaitable[None]] | None = None,
) -> CodeSearchResult:
    """Scrape the top dependents of ``url`` (bounded, non-adaptive) and search their code.

    ``on_scraped`` is awaited with the scrape result before any code search runs. The
    returned ``source`` is always ``url``; ``complete``/``reason`` are copied from the scrape.
    """
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
        on_retry=on_retry,
    )
    if on_scraped is not None:
        await on_scraped(scrape_result)

    if scrape_result.estimated_total_dependents == 0:
        result = CodeSearchResult(source=url, query=query, hits=[], searched_repos=0)
    else:
        result = await search_code(
            session, scrape_result.repos, query, token=token, max_repos=max_repos
        )
    return result.model_copy(
        update={"source": url, "complete": scrape_result.complete, "reason": scrape_result.reason}
    )
