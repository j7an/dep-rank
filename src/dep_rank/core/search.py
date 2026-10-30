"""GitHub code search across dependent repositories."""

from __future__ import annotations

import asyncio
import logging
import time
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
from dep_rank.core.rate_limiter import RETRY_MAX_SECONDS, RateLimiter, retry_after_seconds
from dep_rank.core.scraper import DEFAULT_MAX_PAGES, REQUEST_TIMEOUT, scrape_dependents

if TYPE_CHECKING:
    from dep_rank.core.cache import SqliteCache

SEARCH_URL = "https://api.github.com/search/code"
# GitHub code search: 10 requests/minute authenticated
SEARCH_RATE_LIMITER = RateLimiter(rate=10, period=60.0)
SEARCH_MAX_RETRIES = 2  # per repo; after that the repo and all later ones are "rate limited"
SEARCH_DEFAULT_WAIT = 60.0  # GitHub: wait at least a minute when no header says how long
SEARCH_MIN_WAIT = 1.0  # floor, so a zero wait (clock skew, retry-after: 0) still backs off

logger = logging.getLogger(__name__)


def _rate_limit_wait(resp: httpx2.Response, now: float) -> float | None:
    """Seconds GitHub asks us to wait, or None if ``resp`` is not a rate-limit response.

    A 403 counts only with a rate-limit header; a bare 403 is usually a permission error.
    """
    retry_after = resp.headers.get("retry-after")
    exhausted = resp.headers.get("x-ratelimit-remaining") == "0"
    if resp.status_code != 429 and not (
        resp.status_code == 403 and (retry_after is not None or exhausted)
    ):
        return None
    seconds = retry_after_seconds(retry_after)
    if seconds is not None:
        return max(seconds, 0.0)
    if exhausted:
        try:
            return max(float(resp.headers.get("x-ratelimit-reset", "")) - now, 0.0)
        except ValueError:
            pass
    return SEARCH_DEFAULT_WAIT


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
    failed: dict[str, str] = {}
    incomplete: list[str] = []
    search_repos = repos[:max_repos]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.text-match+json",
    }

    # ponytail: no early stop on 401; add one if large --max-repos with a bad token proves painful.
    rate_limited = False
    for repo in search_repos:
        full_name = f"{repo.owner}/{repo.name}"
        if rate_limited:
            failed[full_name] = "rate limited"
            continue

        search_query = f"{query} repo:{full_name}"
        url = f"{SEARCH_URL}?q={quote(search_query, safe='')}"

        try:
            for retry in range(SEARCH_MAX_RETRIES + 1):
                await SEARCH_RATE_LIMITER.acquire()
                resp = await session.get(
                    url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT
                )
                wait = _rate_limit_wait(resp, time.time())
                if wait is None:
                    break
                delay = max(wait, SEARCH_MIN_WAIT) * 2**retry
                if retry == SEARCH_MAX_RETRIES or delay > RETRY_MAX_SECONDS:
                    rate_limited = True
                    break
                logger.warning(
                    "Code search rate limited on %s — retrying in %.0fs (%d/%d)",
                    full_name,
                    delay,
                    retry + 1,
                    SEARCH_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
            if rate_limited:
                failed[full_name] = "rate limited"
                continue
            if resp.status_code != 200:
                failed[full_name] = f"HTTP {resp.status_code}"
                continue
            data = resp.json()
        except httpx2.RequestError as exc:
            failed[full_name] = type(exc).__name__
            continue
        except ValueError:
            failed[full_name] = "invalid JSON"
            continue

        if data.get("incomplete_results"):
            incomplete.append(full_name)
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
        searched_repos=len(search_repos) - len(failed),
        failed_repos=failed,
        incomplete_repos=incomplete,
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
    package_name: str | None = None,
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
        package_name=package_name,
    )
    if on_scraped is not None:
        await on_scraped(scrape_result)

    result = await search_code(
        session, scrape_result.repos, query, token=token, max_repos=max_repos
    )
    return result.model_copy(
        update={
            "source": url,
            "complete": scrape_result.complete,
            "reason": scrape_result.reason,
            "package": scrape_result.package,
            "packages": scrape_result.packages,
        }
    )
