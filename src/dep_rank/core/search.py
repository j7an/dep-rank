"""GitHub code search across dependent repositories."""

from __future__ import annotations

from urllib.parse import quote

import httpx2

from dep_rank.core.models import CodeSearchHit, CodeSearchResult, Repository
from dep_rank.core.rate_limiter import RateLimiter
from dep_rank.core.scraper import REQUEST_TIMEOUT

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
        source=repos[0].url if repos else "",
        query=query,
        hits=hits,
        searched_repos=len(search_repos),
    )
