"""GitHub code search across dependent repositories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

import aiohttp

from dep_rank.core.models import CodeSearchHit, CodeSearchResult, Repository
from dep_rank.core.rate_limiter import TokenBucketRateLimiter

SEARCH_URL = "https://api.github.com/search/code"
# GitHub code search: 10 requests/minute authenticated
SEARCH_RATE_LIMITER = TokenBucketRateLimiter(rate=10, period=60.0)


async def search_code(
    session: aiohttp.ClientSession,
    repos: list[Repository],
    query: str,
    token: str,
    max_repos: int = 10,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> CodeSearchResult:
    """Search for code patterns across dependent repositories.

    Args:
        session: aiohttp client session.
        repos: List of repositories to search (searched in order, up to max_repos).
        query: Code search query string.
        token: GitHub token (required for code search).
        max_repos: Maximum number of repos to search.
        on_progress: Optional async callback(current, total).
    """
    if not repos:
        return CodeSearchResult(source="", query=query, hits=[], searched_repos=0)

    hits: list[CodeSearchHit] = []
    search_repos = repos[:max_repos]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.text-match+json",
    }

    for i, repo in enumerate(search_repos):
        await SEARCH_RATE_LIMITER.acquire()

        search_query = f"{query} repo:{repo.owner}/{repo.name}"
        url = f"{SEARCH_URL}?q={quote(search_query, safe='')}"

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError):
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

        if on_progress:
            await on_progress(i + 1, len(search_repos))

    return CodeSearchResult(
        source=repos[0].url if repos else "",
        query=query,
        hits=hits,
        searched_repos=len(search_repos),
    )
