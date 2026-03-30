"""GitHub dependents page HTML scraper."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable

import aiohttp
from selectolax.parser import HTMLParser

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import DependentType, Repository
from dep_rank.core.rate_limiter import TokenBucketRateLimiter
from dep_rank.core.validation import validate_github_url

logger = logging.getLogger(__name__)

ITEM_SELECTOR = "#dependents > div.Box > div.flex-items-center"
REPO_SELECTOR = "span > a.text-bold"
STARS_SELECTOR = "div > div > span"
NEXT_BUTTON_SELECTOR = "#dependents > div.paginate-container > div > a"
GITHUB_URL = "https://github.com"
MAX_PAGES = 1000
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
CACHE_TTL = 86400  # 24 hours
DEPENDENTS_PER_PAGE = 30  # Approximate dependents shown per GitHub page
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 120
_RATE_LIMITER_UNAUTH = TokenBucketRateLimiter(rate=10, period=60.0)
_RATE_LIMITER_AUTH = TokenBucketRateLimiter(rate=60, period=60.0)


def parse_dependents_page(html: str) -> tuple[list[Repository], str | None]:
    """Parse a single GitHub dependents HTML page.

    Returns:
        Tuple of (list of repositories, next page URL or None).
    """
    tree = HTMLParser(html)
    repos: list[Repository] = []

    for item in tree.css(ITEM_SELECTOR):
        repo_node = item.css_first(REPO_SELECTOR)
        stars_node = item.css_first(STARS_SELECTOR)

        if not repo_node or not stars_node:
            continue

        href = repo_node.attributes.get("href", "")
        if not href:
            continue

        stars_text = stars_node.text(strip=True)
        try:
            stars = int(stars_text.replace(",", ""))
        except (ValueError, AttributeError):
            continue

        parts = href.strip("/").split("/")
        if len(parts) != 2:
            continue

        owner, name = parts
        repos.append(
            Repository(
                owner=owner,
                name=name,
                url=f"{GITHUB_URL}/{owner}/{name}",
                stars=stars,
            )
        )

    # Find next page URL
    next_url: str | None = None
    links = tree.css(NEXT_BUTTON_SELECTOR)
    if len(links) == 2:
        next_href = links[1].attributes.get("href")
        if next_href:
            next_url = f"{GITHUB_URL}{next_href}" if next_href.startswith("/") else next_href
    elif len(links) == 1:
        link_text = links[0].text(strip=True)
        if link_text == "Next":
            next_href = links[0].attributes.get("href")
            if next_href:
                next_url = f"{GITHUB_URL}{next_href}" if next_href.startswith("/") else next_href

    return repos, next_url


def parse_dependent_counts(html: str) -> dict[str, int]:
    """Parse Repository and Package dependent counts from the dependents page header.

    Returns:
        Dict mapping "REPOSITORY" and/or "PACKAGE" to their counts.
        Returns empty dict if parsing fails.
    """
    tree = HTMLParser(html)
    counts: dict[str, int] = {}

    for link in tree.css("div.table-list-header-toggle a.btn-link"):
        text = link.text(strip=True)
        # Text is like "2,295,450 Repositories" or "44,317 Packages"
        match = re.match(r"([\d,]+)\s+(Repositories|Packages)", text)
        if match:
            count = int(match.group(1).replace(",", ""))
            kind = match.group(2)
            key = "REPOSITORY" if kind == "Repositories" else "PACKAGE"
            counts[key] = count

    return counts


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with full jitter.

    delay = random(0, min(base * 2^attempt, max))
    """
    exp = min(RETRY_BASE_SECONDS * (2**attempt), RETRY_MAX_SECONDS)
    return random.uniform(0, exp)  # noqa: S311


async def _fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    rate_limiter: TokenBucketRateLimiter,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
) -> tuple[str | None, str | None]:
    """Fetch a single page with rate limiting, retries, and caching.

    Returns:
        (html_content, etag) or (None, None) on failure.
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    headers: dict[str, str] = {}
    cached_body: bytes | None = None
    if cache:
        cached = await cache.get(url)
        if cached:
            if cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            cached_body = cached["body"]

    for attempt in range(MAX_RETRIES + 1):
        await rate_limiter.acquire()
        try:
            async with session.get(
                url,
                timeout=timeout,
                headers={**auth_headers, **headers},
            ) as resp:
                if resp.status == 304 and cache and cached_body:
                    await cache.put(
                        url,
                        cached_body,
                        etag=headers.get("If-None-Match"),
                        ttl=CACHE_TTL,
                    )
                    return cached_body.decode("utf-8"), headers.get("If-None-Match")
                elif resp.status == 200:
                    body = await resp.read()
                    html = body.decode("utf-8")
                    etag = resp.headers.get("ETag")
                    if cache:
                        await cache.put(url, body, etag=etag, ttl=CACHE_TTL)
                    return html, etag
                elif resp.status == 429:
                    delay = _retry_delay(attempt)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        delay = max(delay, float(retry_after))
                    logger.warning(
                        "Rate limited — retrying in %.1fs (%d/%d)",
                        delay,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning("Unexpected HTTP %d — stopping", resp.status)
                    return None, None
        except (TimeoutError, aiohttp.ClientError):
            delay = _retry_delay(attempt)
            logger.warning(
                "Request failed — retrying in %.1fs (%d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            await asyncio.sleep(delay)

    logger.warning("Exhausted retries for %s", url)
    return None, None


async def scrape_dependents(
    session: aiohttp.ClientSession,
    url: str,
    dependent_type: DependentType = DependentType.REPOSITORY,
    min_stars: int = 5,
    cache: SqliteCache | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    token: str | None = None,
    max_pages: int = MAX_PAGES,
) -> list[Repository]:
    """Scrape GitHub dependents pages and return repositories sorted by stars.

    Uses a prefetch pipeline: while processing page N, page N+1 is already
    being fetched. Cache hits skip the network entirely.

    Args:
        session: aiohttp client session.
        url: GitHub repository URL.
        dependent_type: REPOSITORY or PACKAGE.
        min_stars: Minimum star count to include.
        cache: Optional SQLite cache for HTTP responses.
        on_progress: Optional async callback(current_page, estimated_total_pages).
        token: Optional GitHub token for authenticated scraping (higher rate limits).
        max_pages: Maximum number of pages to scrape (default: 1000).
    """
    owner, repo = validate_github_url(url)
    base_url = f"{GITHUB_URL}/{owner}/{repo}/network/dependents"
    current_url: str | None = f"{base_url}?dependent_type={dependent_type.value}"

    rate_limiter = _RATE_LIMITER_AUTH if token else _RATE_LIMITER_UNAUTH
    auth_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}

    all_repos: list[Repository] = []
    seen_urls: set[str] = set()
    source_url = f"{GITHUB_URL}/{owner}/{repo}"
    page = 0
    prefetch_task: asyncio.Task[tuple[str | None, str | None]] | None = None

    while current_url and page < max_pages:
        page += 1

        # Check cache first
        html: str | None = None
        cache_hit = False
        if cache:
            cached = await cache.get(current_url)
            if cached and cached["body"] is not None and not cached.get("expired"):
                html = cached["body"].decode("utf-8")
                cache_hit = True

        if html is None:
            # Use prefetched result if available, otherwise fetch now
            if prefetch_task is not None:
                html, _etag = await prefetch_task
                prefetch_task = None
            else:
                html, _etag = await _fetch_page(
                    session,
                    current_url,
                    rate_limiter,
                    auth_headers,
                    cache,
                )

            if html is None:
                break

        # Parse current page
        repos, next_url = parse_dependents_page(html)

        # Start prefetching next page if it's not cached
        if next_url and prefetch_task is None:
            next_cached = False
            if cache:
                next_entry = await cache.get(next_url)
                next_cached = bool(
                    next_entry and next_entry["body"] is not None and not next_entry.get("expired")
                )
            if not next_cached:
                prefetch_task = asyncio.create_task(
                    _fetch_page(session, next_url, rate_limiter, auth_headers, cache)
                )

        for repo_obj in repos:
            if repo_obj.url in seen_urls or repo_obj.url == source_url:
                continue
            if repo_obj.stars >= min_stars:
                seen_urls.add(repo_obj.url)
                all_repos.append(repo_obj)

        logger.debug("Page %d: %s", page, "cache hit" if cache_hit else "network fetch")
        if on_progress:
            await on_progress(page, 0)

        current_url = next_url

    # Cancel any outstanding prefetch
    if prefetch_task is not None:
        prefetch_task.cancel()
        try:
            await prefetch_task
        except asyncio.CancelledError:
            pass  # Expected when scraping ends before prefetch completes

    all_repos.sort(key=lambda r: r.stars, reverse=True)
    return all_repos
