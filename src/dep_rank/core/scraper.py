"""GitHub dependents page HTML scraper."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import math
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple
from urllib.parse import urljoin, urlsplit

import httpx2
from selectolax.parser import HTMLParser

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import (
    DependentType,
    Repository,
    RetryStatus,
    ScrapeReason,
    ScrapeResult,
    ScrapeSnapshot,
)
from dep_rank.core.rate_limiter import (
    AUTH_RATE,
    RATE_PERIOD,
    UNAUTH_RATE,
    RateLimiter,
    backoff_delay,
)
from dep_rank.core.validation import validate_github_url

logger = logging.getLogger(__name__)

ITEM_SELECTOR = "#dependents > div.Box > div.flex-items-center"
REPO_SELECTOR = "span > a.text-bold"
STARS_SELECTOR = "div > div > span"
NEXT_BUTTON_SELECTOR = "#dependents > div.paginate-container > div > a"
GITHUB_URL = "https://github.com"
MAX_RETRIES = 5
# ponytail: per-phase inactivity timeout; add asyncio.timeout if a request is observed exceeding REQUEST_TIMEOUT overall.  # noqa: E501
REQUEST_TIMEOUT = 30
CACHE_TTL = 86400  # 24 hours
DEPENDENTS_PER_PAGE = 30  # Approximate dependents shown per GitHub page
MAX_PAGES_CEILING = 1000
DEFAULT_MAX_PAGES = 200
ADAPTIVE_WINDOW = 20  # trailing pages examined for the trend
ADAPTIVE_W_MIN = 30  # minimum pages before adaptive stop may fire

SWR_COOLDOWN = 300.0  # seconds a URL serves stale without re-refreshing after a failure
SWR_DRAIN_TIMEOUT = 10.0  # seconds to await outstanding refreshes before cancelling
SWR_HEADROOM_TOKENS = 2  # a background refresh consumes a token only when >= this many remain
#                          (try_acquire reserve=SWR_HEADROOM_TOKENS-1, leaving >=1 for foreground)


class NetworkFailureError(Exception):
    """A page could not be fetched after the retry budget (or an unexpected status)."""


class RateLimitedError(Exception):
    """The retry budget was exhausted on 429 responses."""


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

    # Find next page URL: the second of two pagination links, or a lone "Next" link.
    links = tree.css(NEXT_BUTTON_SELECTOR)
    if len(links) == 2:
        next_link = links[1]
    elif len(links) == 1 and links[0].text(strip=True) == "Next":
        next_link = links[0]
    else:
        return repos, None
    href = next_link.attributes.get("href")
    if not href:
        return repos, None
    next_url = urljoin(GITHUB_URL, href)
    # The next request carries the auth token: only follow links back to https://github.com.
    target = urlsplit(next_url)
    if target.scheme != "https" or target.netloc != "github.com":
        return repos, None
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
        # Text is like "2,295,450 Repositories" or "44,317 Packages" (or singular forms)
        match = re.match(r"([\d,]+)\s+(Repositor(?:ies|y)|Packages?)\s*$", text)
        if match:
            count = int(match.group(1).replace(",", ""))
            kind = match.group(2)
            key = "REPOSITORY" if kind.startswith("Repositor") else "PACKAGE"
            counts[key] = count

    return counts


class _Attempt(NamedTuple):
    status: int
    body: bytes | None  # 200, or 304 with a cached body
    retry_delay: float = 0.0  # 429 only (from limiter.note_429)


async def _get_once(
    session: httpx2.AsyncClient,
    url: str,
    limiter: RateLimiter,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
    cached: dict[str, Any] | None,
) -> _Attempt:
    """Make one GET, feed the outcome to the limiter, and store a usable body in the cache.

    Shared by the foreground walk and SWR refreshes, so it must not await anything before
    ``session.stream`` (callers' pause checks rely on that).
    """
    headers = dict(auth_headers)
    if cached and cached["etag"]:
        headers["If-None-Match"] = cached["etag"]
    async with session.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT
    ) as resp:
        if resp.status_code == 200:
            body: bytes = await resp.aread()
            etag = resp.headers.get("ETag")
        elif resp.status_code == 304 and cached and cached["body"] is not None:
            body = cached["body"]
            etag = cached["etag"]
        elif resp.status_code == 429:
            try:
                retry_after: float | None = float(resp.headers.get("Retry-After", ""))
            except ValueError:  # absent, or the HTTP-date form
                retry_after = None
            if retry_after is not None and not math.isfinite(retry_after):
                retry_after = None  # "inf" would sleep forever
            return _Attempt(429, None, limiter.note_429(retry_after))
        else:
            return _Attempt(resp.status_code, None)
    limiter.note_success()
    if cache:
        await cache.put(url, body, etag=etag, ttl=CACHE_TTL)
    return _Attempt(resp.status_code, body)


async def _fetch_page(
    session: httpx2.AsyncClient,
    url: str,
    limiter: RateLimiter,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
    on_retry: Callable[[int, float], Awaitable[None]],
) -> str:
    """Fetch one page with rate limiting, retries, and caching.

    Returns the HTML body. Raises RateLimitedError or NetworkFailureError when the
    retry budget is exhausted or an unexpected status is returned. Each 429 retry is
    reported to ``on_retry(attempt, delay)`` before its wait.
    """
    rate_limited = False
    for attempt in range(MAX_RETRIES + 1):
        await limiter.acquire()
        try:
            result = await _get_once(session, url, limiter, auth_headers, cache, cached=None)
        except httpx2.RequestError:
            if attempt == MAX_RETRIES:
                break
            delay = backoff_delay(attempt)
            logger.warning(
                "Request failed — retrying in %.1fs (%d/%d)", delay, attempt + 1, MAX_RETRIES
            )
            await asyncio.sleep(delay)
            continue
        if result.body is not None:
            return result.body.decode("utf-8")
        if result.status == 429:
            rate_limited = True
            if attempt == MAX_RETRIES:
                break
            await on_retry(attempt + 1, result.retry_delay)
            await asyncio.sleep(result.retry_delay)
            continue
        logger.warning("Unexpected HTTP %d — stopping", result.status)
        raise NetworkFailureError(f"HTTP {result.status} for {url}")
    logger.warning("Exhausted retries for %s", url)
    if rate_limited:
        raise RateLimitedError(url)
    raise NetworkFailureError(url)


async def _read_page(
    session: httpx2.AsyncClient,
    url: str,
    limiter: RateLimiter,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
    swr: SWRManager,
    on_retry: Callable[[int, float], Awaitable[None]],
) -> tuple[str, bool]:
    """Return ``(html, stale)``; fresh-hit skips network, stale-hit serves stale + refreshes.

    Fresh cache hit -> cached body (no request). Expired-with-body -> serve stale
    immediately (``stale`` is True) and ask ``swr`` for a background revalidation (a
    no-op when SWR is disabled). Miss -> ``_fetch_page``.
    """
    if cache:
        cached = await cache.get(url)
        if cached and cached["body"] is not None:
            body: bytes = cached["body"]
            stale = bool(cached.get("expired"))
            if stale:
                swr.schedule(url)
            return body.decode("utf-8"), stale
    return await _fetch_page(session, url, limiter, auth_headers, cache, on_retry), False


class SWRManager:
    """Owns background stale-while-revalidate refreshes for one scrape.

    Disabled when unauthenticated (refresh would displace the foreground walk under
    the 1/min budget). Refreshes are deduped per URL, capped at one in flight,
    paused after a recent 429, gated *at refresh time* on
    foreground token headroom (via ``try_acquire(reserve=...)``), cooled down on
    failure, and drained before ``scrape_dependents`` returns (while the session is still
    open) — never at cache.close(). Refresh outcomes feed the shared limiter
    (``note_success`` on 200/304, ``note_429`` on 429), so background rate pressure
    throttles both background and foreground work.
    """

    def __init__(
        self,
        session: httpx2.AsyncClient,
        limiter: RateLimiter,
        auth_headers: dict[str, str],
        cache: SqliteCache | None,
        *,
        enabled: bool,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._limiter = limiter
        self._auth_headers = auth_headers
        self._cache = cache
        self._enabled = enabled and cache is not None
        self._now = now
        self._semaphore = asyncio.Semaphore(1)
        self._inflight: set[str] = set()
        self._cooldown_until: dict[str, float] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, url: str) -> None:
        """Schedule a background refresh if enabled, not deduped, off cooldown, and not paused.

        Token headroom is deliberately **not** checked here. A schedule-time
        availability read is lock-unaware and races the foreground walk
        (tokens read here can be gone by the time the refresh runs). Instead the
        token decision is made atomically at refresh time via
        ``try_acquire(reserve=SWR_HEADROOM_TOKENS - 1)``, which both reserves
        headroom for the foreground and consumes in one lock-aware step.
        """
        if not self._enabled or url in self._inflight:
            return
        until = self._cooldown_until.get(url)
        if until is not None and self._now() < until:
            return
        if self._limiter.background_paused():
            # Yield to foreground retries after a recent 429.
            return
        self._inflight.add(url)
        task = asyncio.create_task(self._refresh(url))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh(self, url: str) -> None:
        try:
            async with self._semaphore:
                if self._limiter.background_paused():
                    return
                # Atomically consume a token only if foreground headroom remains;
                # if not (foreground drained the bucket), abort without making a request.
                if not self._limiter.try_acquire(reserve=SWR_HEADROOM_TOKENS - 1):
                    return
                cached = await self._cache.get(url) if self._cache else None
                # Re-check here, not in `_get_once` (shared with the foreground): a
                # foreground 429 may have landed during the cache lookup above.
                if self._limiter.background_paused():
                    return
                result = await _get_once(
                    self._session, url, self._limiter, self._auth_headers, self._cache, cached
                )
                if result.body is None:
                    self._cool_down(url, f"HTTP {result.status}")
        except httpx2.RequestError as exc:
            self._cool_down(url, exc.__class__.__name__)
        finally:
            self._inflight.discard(url)

    def _cool_down(self, url: str, why: str) -> None:
        self._cooldown_until[url] = self._now() + SWR_COOLDOWN
        logger.warning("SWR refresh for %s failed (%s); cooling down %.0fs", url, why, SWR_COOLDOWN)

    async def drain(self, timeout: float = SWR_DRAIN_TIMEOUT) -> None:
        """Await outstanding refreshes up to ``timeout``, then cancel any stragglers."""
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _heap_push(
    heap: list[tuple[int, int, Repository]], repo: Repository, count: int, rows: int
) -> None:
    """Maintain a bounded min-heap of the top-``rows`` repos by stars.

    ``rows <= 0`` keeps none.

    Tie-handling: ``count`` is the monotonically increasing arrival index, stored
    **negated** so that among repos with equal stars the min-heap root (the eviction
    candidate) is the *latest-seen* one. Combined with the strict ``>`` admission test
    below — a new repo whose stars merely *tie* the current minimum is not admitted —
    this guarantees "ties: earlier-seen wins" both on admission and on eviction.
    """
    entry = (repo.stars, -count, repo)
    if rows <= 0:
        return
    if len(heap) < rows:
        heapq.heappush(heap, entry)
    elif repo.stars > heap[0][0]:
        heapq.heapreplace(heap, entry)


def _top_k(heap: list[tuple[int, int, Repository]]) -> list[Repository]:
    """Return the heap's repos sorted by stars descending (ties: earlier-seen first).

    Heap entries store the arrival index negated (``-count``), so to order ties
    earliest-first we sort ascending on the *original* index ``-e[1]``.
    """
    return [r for _, _, r in sorted(heap, key=lambda e: (-e[0], -e[1]))]


def _should_stop(
    heap: list[tuple[int, int, Repository]],
    rows: int,
    recent_max: deque[int],
    page: int,
) -> bool:
    """True when the trailing window can no longer plausibly change the saturated top-K."""
    if rows <= 0:
        return False
    if len(heap) < rows:  # heap not saturated -> kth_best undefined
        return False
    if page < ADAPTIVE_W_MIN:
        return False
    if len(recent_max) < ADAPTIVE_WINDOW:
        return False
    kth_best = heap[0][0]  # min of the size-rows heap == K-th best stars
    return max(recent_max) < kth_best


async def scrape_dependents(
    session: httpx2.AsyncClient,
    url: str,
    *,
    rows: int,
    dependent_type: DependentType = DependentType.REPOSITORY,
    min_stars: int = 5,
    cache: SqliteCache | None = None,
    token: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    adaptive_stop: bool = True,
    rate_limiter: RateLimiter | None = None,
    on_page: Callable[[ScrapeSnapshot], Awaitable[None]] | None = None,
    on_retry: Callable[[RetryStatus], Awaitable[None]] | None = None,
) -> ScrapeResult:
    """Walk the dependents pages, keeping the top-``rows`` repos by stars.

    ``on_page`` is awaited after each consumed page with the running top-K. ``on_retry``
    is awaited before each rate-limit wait, replacing the warning log line. Exceptions
    they raise propagate. Outstanding background SWR refreshes are drained before returning
    (or raising), while the caller's session is still open.
    """
    owner, repo = validate_github_url(url)
    base_url = f"{GITHUB_URL}/{owner}/{repo}/network/dependents"
    current_url: str | None = f"{base_url}?dependent_type={dependent_type.value}"
    source_url = f"{GITHUB_URL}/{owner}/{repo}"

    max_pages = min(max_pages, MAX_PAGES_CEILING)
    limiter = rate_limiter or RateLimiter(AUTH_RATE if token else UNAUTH_RATE, RATE_PERIOD)
    auth_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}

    swr = SWRManager(session, limiter, auth_headers, cache, enabled=bool(token))

    heap: list[tuple[int, int, Repository]] = []
    counter = itertools.count()
    seen: set[str] = set()
    matched = 0
    est_pages = 0
    est_deps = 0
    recent_max: deque[int] = deque(maxlen=ADAPTIVE_WINDOW)
    page = 0
    stale_pages = 0
    reason: ScrapeReason | None = None

    async def report_retry(attempt: int, delay: float) -> None:
        status = RetryStatus(page=page + 1, attempt=attempt, max_retries=MAX_RETRIES, delay=delay)
        if on_retry:
            await on_retry(status)
        else:
            logger.warning(
                "Rate limited on page %d — retrying in %.1fs (%d/%d)",
                status.page,
                delay,
                attempt,
                MAX_RETRIES,
            )

    try:
        while current_url and page < max_pages:
            try:
                html, stale = await _read_page(
                    session,
                    current_url,
                    limiter,
                    auth_headers,
                    cache,
                    swr,
                    report_retry,
                )
            except RateLimitedError:
                reason = ScrapeReason.RATE_LIMITED
                break
            except NetworkFailureError:
                reason = ScrapeReason.NETWORK_FAILURE
                break
            page += 1  # increment only after a page is actually consumed (spec §2)
            stale_pages += stale

            if page == 1:
                counts = parse_dependent_counts(html)
                est_deps = counts.get(dependent_type.value, 0)
                est_pages = est_deps // DEPENDENTS_PER_PAGE if est_deps > 0 else 0

            repos, next_url = parse_dependents_page(html)
            page_max = 0
            for repo_obj in repos:
                if repo_obj.url in seen or repo_obj.url == source_url:
                    continue
                if repo_obj.stars >= min_stars:
                    seen.add(repo_obj.url)
                    matched += 1
                    page_max = max(page_max, repo_obj.stars)
                    _heap_push(heap, repo_obj, next(counter), rows)
            recent_max.append(page_max)

            if on_page:
                await on_page(
                    ScrapeSnapshot(
                        top_k=_top_k(heap),
                        pages_scraped=page,
                        estimated_total_pages=est_pages,
                        estimated_total_dependents=est_deps,
                        matched_count=matched,
                    )
                )

            if adaptive_stop and _should_stop(heap, rows, recent_max, page):
                reason = ScrapeReason.TREND_CONVERGED
                break
            current_url = next_url
        else:
            # Loop exited via its condition (no break): exhausted, unless we stopped at the cap.
            if current_url is not None and page >= max_pages:
                reason = ScrapeReason.MAX_PAGES_REACHED
    finally:
        await swr.drain()

    # `estimated_total_pages` is always the header-derived estimate, never the walked count
    # (which lives in `pages_scraped`).
    return ScrapeResult(
        repos=_top_k(heap),
        pages_scraped=page,
        max_pages=max_pages,
        estimated_total_pages=est_pages,
        estimated_total_dependents=est_deps,
        complete=reason is None,
        reason=reason,
        matched_count=matched,
        stale_pages=stale_pages,
    )
