"""GitHub dependents page HTML scraper."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import random
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable

import aiohttp
from selectolax.parser import HTMLParser

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import (
    DependentType,
    Repository,
    ScrapeReason,
    ScrapeResult,
    ScrapeSnapshot,
)
from dep_rank.core.rate_limiter import AdaptiveRateLimiter
from dep_rank.core.validation import validate_github_url

logger = logging.getLogger(__name__)

ITEM_SELECTOR = "#dependents > div.Box > div.flex-items-center"
REPO_SELECTOR = "span > a.text-bold"
STARS_SELECTOR = "div > div > span"
NEXT_BUTTON_SELECTOR = "#dependents > div.paginate-container > div > a"
GITHUB_URL = "https://github.com"
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
CACHE_TTL = 86400  # 24 hours
DEPENDENTS_PER_PAGE = 30  # Approximate dependents shown per GitHub page
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 120
MAX_PAGES_CEILING = 1000
DEFAULT_MAX_PAGES = 200
DEFAULT_CONCURRENCY = 3
ADAPTIVE_WINDOW = 20  # trailing pages examined for the trend
ADAPTIVE_W_MIN = 30  # minimum pages before adaptive stop may fire
MAX_PAGES = MAX_PAGES_CEILING  # back-compat: cli.app's search progress bar imports this

SWR_COOLDOWN = 300.0  # seconds a URL serves stale without re-refreshing after a failure
SWR_DRAIN_TIMEOUT = 10.0  # seconds to await outstanding refreshes before cancelling
SWR_HEADROOM_TOKENS = 2  # a background refresh consumes a token only when >= this many remain
#                          (try_acquire reserve=SWR_HEADROOM_TOKENS-1, leaving >=1 for foreground)


class ScrapeError(Exception):
    """Base class for terminal scrape failures."""


class NetworkFailureError(ScrapeError):
    """A page could not be fetched after the retry budget (or an unexpected status)."""


class RateLimitedError(ScrapeError):
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
        # Text is like "2,295,450 Repositories" or "44,317 Packages" (or singular forms)
        match = re.match(r"([\d,]+)\s+(Repositor(?:ies|y)|Packages?)\s*$", text)
        if match:
            count = int(match.group(1).replace(",", ""))
            kind = match.group(2)
            key = "REPOSITORY" if kind.startswith("Repositor") else "PACKAGE"
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
    limiter: AdaptiveRateLimiter,
    semaphore: asyncio.Semaphore,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
) -> str:
    """Fetch one page with bounded concurrency, rate limiting, retries, and caching.

    Returns the HTML body. Raises RateLimitedError or NetworkFailureError when the
    retry budget is exhausted or an unexpected status is returned.
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

    rate_limited = False
    for attempt in range(MAX_RETRIES + 1):
        async with semaphore:
            await limiter.acquire()
            try:
                async with session.get(
                    url, timeout=timeout, headers={**auth_headers, **headers}
                ) as resp:
                    if resp.status == 304 and cache and cached_body:
                        limiter.note_success()
                        await cache.put(
                            url, cached_body, etag=headers.get("If-None-Match"), ttl=CACHE_TTL
                        )
                        return cached_body.decode("utf-8")
                    if resp.status == 200:
                        limiter.note_success()
                        body: bytes = await resp.read()
                        if cache:
                            await cache.put(url, body, etag=resp.headers.get("ETag"), ttl=CACHE_TTL)
                        return body.decode("utf-8")
                    if resp.status == 429:
                        rate_limited = True
                        retry_after = resp.headers.get("Retry-After")
                        delay = limiter.note_429(float(retry_after) if retry_after else None)
                        logger.warning(
                            "Rate limited — retrying in %.1fs (%d/%d)",
                            delay,
                            attempt + 1,
                            MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Unexpected HTTP %d — stopping", resp.status)
                    raise NetworkFailureError(f"HTTP {resp.status} for {url}")
            except (TimeoutError, aiohttp.ClientError):
                delay = _retry_delay(attempt)
                logger.warning(
                    "Request failed — retrying in %.1fs (%d/%d)", delay, attempt + 1, MAX_RETRIES
                )
                await asyncio.sleep(delay)

    logger.warning("Exhausted retries for %s", url)
    if rate_limited:
        raise RateLimitedError(url)
    raise NetworkFailureError(url)


async def _read_page(
    session: aiohttp.ClientSession,
    url: str,
    limiter: AdaptiveRateLimiter,
    semaphore: asyncio.Semaphore,
    auth_headers: dict[str, str],
    cache: SqliteCache | None,
    swr: SWRManager | None = None,
) -> str:
    """Return a page's HTML; fresh-hit skips network, stale-hit serves stale + refreshes."""
    if cache:
        cached = await cache.get(url)
        if cached and cached["body"] is not None:
            body: bytes = cached["body"]
            if not cached.get("expired"):
                return body.decode("utf-8")
            if swr is not None:
                swr.schedule(url)
                return body.decode("utf-8")
    return await _fetch_page(session, url, limiter, semaphore, auth_headers, cache)


class SWRManager:
    """Owns background stale-while-revalidate refreshes for one scrape.

    Disabled when unauthenticated (refresh would displace the foreground walk under
    the 1/min budget). Refreshes are deduped per URL, capped at one in flight,
    suppressed while AIMD has concurrency at its floor, gated *at refresh time* on
    foreground token headroom (via ``try_acquire(reserve=...)``), cooled down on
    failure, and drained before the generator completes (while the session is still
    open) — never at cache.close(). Refresh outcomes feed the shared limiter
    (``note_success`` on 200/304, ``note_429`` on 429), so background rate pressure
    throttles both background and foreground work.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        limiter: AdaptiveRateLimiter,
        auth_headers: dict[str, str],
        cache: SqliteCache | None,
        *,
        enabled: bool,
        cooldown: float = SWR_COOLDOWN,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._limiter = limiter
        self._auth_headers = auth_headers
        self._cache = cache
        self._enabled = enabled and cache is not None
        self._cooldown = cooldown
        self._now = now
        self._semaphore = asyncio.Semaphore(1)
        self._inflight: set[str] = set()
        self._cooldown_until: dict[str, float] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, url: str) -> None:
        """Schedule a background refresh if enabled, not deduped, off cooldown, and AIMD allows it.

        Token headroom is deliberately **not** checked here. A schedule-time
        ``tokens_available()`` read is lock-unaware and races the foreground walk
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
        if self._limiter.current_max_concurrency <= 1:
            # AIMD has throttled concurrency to its floor on sustained 429s —
            # suppress background work so it cannot compete with foreground retries.
            return
        self._inflight.add(url)
        task = asyncio.create_task(self._refresh(url))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh(self, url: str) -> None:
        try:
            async with self._semaphore:
                # Atomically consume a token only if foreground headroom remains;
                # if not (foreground drained the bucket), abort without making a request.
                if not self._limiter.try_acquire(reserve=SWR_HEADROOM_TOKENS - 1):
                    return
                cached = await self._cache.get(url) if self._cache else None
                headers = dict(self._auth_headers)
                if cached and cached["etag"]:
                    headers["If-None-Match"] = cached["etag"]
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                async with self._session.get(url, timeout=timeout, headers=headers) as resp:
                    if resp.status == 200:
                        self._limiter.note_success()
                        body = await resp.read()
                        if self._cache:
                            await self._cache.put(
                                url, body, etag=resp.headers.get("ETag"), ttl=CACHE_TTL
                            )
                    elif resp.status == 304 and cached and cached["body"] is not None:
                        self._limiter.note_success()
                        if self._cache:
                            await self._cache.put(
                                url, cached["body"], etag=cached["etag"], ttl=CACHE_TTL
                            )
                    elif resp.status == 429:
                        # Feed the *shared* limiter so AIMD halves concurrency — which
                        # suppresses further background refreshes (the schedule gate) and
                        # lengthens foreground backoff. Mirrors the foreground 429 path in
                        # `_fetch_page`. The returned delay is unused: the background path
                        # waits via the per-URL cooldown below, not an in-band sleep.
                        retry_after = resp.headers.get("Retry-After")
                        self._limiter.note_429(float(retry_after) if retry_after else None)
                        self._cooldown_until[url] = self._now() + self._cooldown
                        logger.warning(
                            "SWR refresh rate-limited (429) for %s; cooling down %.0fs",
                            url,
                            self._cooldown,
                        )
                    else:
                        self._cooldown_until[url] = self._now() + self._cooldown
                        logger.warning(
                            "SWR refresh got unexpected status %d for %s; cooling down %.0fs",
                            resp.status,
                            url,
                            self._cooldown,
                        )
        except (TimeoutError, aiohttp.ClientError) as exc:
            self._cooldown_until[url] = self._now() + self._cooldown
            logger.warning(
                "SWR refresh failed for %s (%s); cooling down %.0fs",
                url,
                exc.__class__.__name__,
                self._cooldown,
            )
        finally:
            self._inflight.discard(url)

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
    heap: list[tuple[int, int, Repository]], repo: Repository, count: int, rows: int | None
) -> None:
    """Maintain a bounded min-heap of the top-``rows`` repos by stars.

    ``rows is None`` keeps every repo (unbounded, back-compat). ``rows <= 0`` keeps none.

    Tie-handling: ``count`` is the monotonically increasing arrival index, stored
    **negated** so that among repos with equal stars the min-heap root (the eviction
    candidate) is the *latest-seen* one. Combined with the strict ``>`` admission test
    below — a new repo whose stars merely *tie* the current minimum is not admitted —
    this guarantees "ties: earlier-seen wins" both on admission and on eviction.
    """
    entry = (repo.stars, -count, repo)
    if rows is None:
        heap.append(entry)
    elif rows <= 0:
        return
    elif len(heap) < rows:
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
    rows: int | None,
    recent_max: deque[int],
    page: int,
) -> bool:
    """True when the trailing window can no longer plausibly change the saturated top-K."""
    if rows is None or rows <= 0:
        return False
    if len(heap) < rows:  # heap not saturated -> kth_best undefined
        return False
    if page < ADAPTIVE_W_MIN:
        return False
    if len(recent_max) < ADAPTIVE_WINDOW:
        return False
    kth_best = heap[0][0]  # min of the size-rows heap == K-th best stars
    return max(recent_max) < kth_best


def _build_snapshot(
    heap: list[tuple[int, int, Repository]],
    page: int,
    est_pages: int,
    est_deps: int,
    matched: int,
    *,
    done: bool,
    reason: ScrapeReason | None = None,
) -> ScrapeSnapshot:
    return ScrapeSnapshot(
        top_k=_top_k(heap),
        pages_scraped=page,
        estimated_total_pages=est_pages,
        estimated_total_dependents=est_deps,
        matched_count=matched,
        done=done,
        complete=(reason is None) if done else False,
        reason=reason if done else None,
    )


async def stream_dependents(
    session: aiohttp.ClientSession,
    url: str,
    *,
    rows: int | None,
    dependent_type: DependentType = DependentType.REPOSITORY,
    min_stars: int = 5,
    cache: SqliteCache | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    token: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    concurrency: int = DEFAULT_CONCURRENCY,
    adaptive_stop: bool = True,
    rate_limiter: AdaptiveRateLimiter | None = None,
) -> AsyncIterator[ScrapeSnapshot]:
    """Stream top-K dependents page by page.

    Yields one snapshot per consumed page (done=False) then exactly one terminal
    snapshot (done=True) carrying complete/reason. ``rows is None`` keeps every
    matched repo and disables adaptive stop (back-compat for the wrapper).
    """
    if not 1 <= concurrency <= 10:
        msg = f"concurrency must be between 1 and 10, got {concurrency}"
        raise ValueError(msg)
    owner, repo = validate_github_url(url)
    base_url = f"{GITHUB_URL}/{owner}/{repo}/network/dependents"
    current_url: str | None = f"{base_url}?dependent_type={dependent_type.value}"
    source_url = f"{GITHUB_URL}/{owner}/{repo}"

    max_pages = min(max_pages, MAX_PAGES_CEILING)
    limiter = rate_limiter or AdaptiveRateLimiter.for_token(token, concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    auth_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}

    swr = SWRManager(session, limiter, auth_headers, cache, enabled=bool(token))

    heap: list[tuple[int, int, Repository]] = []
    counter = itertools.count()
    seen: set[str] = set()
    matched = 0
    est_pages = 0
    est_deps = 0
    recent_max: deque[int] = deque(maxlen=ADAPTIVE_WINDOW)
    bounded = rows is not None
    page = 0
    reason: ScrapeReason | None = None

    try:
        while current_url and page < max_pages:
            try:
                html = await _read_page(
                    session, current_url, limiter, semaphore, auth_headers, cache, swr=swr
                )
            except RateLimitedError:
                reason = ScrapeReason.RATE_LIMITED
                break
            except NetworkFailureError:
                reason = ScrapeReason.NETWORK_FAILURE
                break
            page += 1  # increment only after a page is actually consumed (spec §2)

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

            if on_progress:
                await on_progress(page, est_pages)
            yield _build_snapshot(heap, page, est_pages, est_deps, matched, done=False)

            if adaptive_stop and bounded and _should_stop(heap, rows, recent_max, page):
                reason = ScrapeReason.TREND_CONVERGED
                break
            current_url = next_url
        else:
            # Loop exited via its condition (no break): exhausted, unless we stopped at the cap.
            if current_url is not None and page >= max_pages:
                reason = ScrapeReason.MAX_PAGES_REACHED

        # `estimated_total_pages` is always the header-derived estimate (`est_pages`),
        # matching the historical scraper (it returned the header estimate unconditionally).
        # Actual pages consumed live in `pages_scraped`; do NOT overwrite the estimate with
        # `page` on completion — Phase 4 summaries and existing header-estimate tests depend
        # on the estimate staying the population projection, not the walked count.
        yield _build_snapshot(heap, page, est_pages, est_deps, matched, done=True, reason=reason)
    finally:
        await swr.drain()


async def scrape_dependents(
    session: aiohttp.ClientSession,
    url: str,
    dependent_type: DependentType = DependentType.REPOSITORY,
    min_stars: int = 5,
    cache: SqliteCache | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    token: str | None = None,
    max_pages: int = MAX_PAGES_CEILING,
    *,
    rows: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    adaptive_stop: bool = True,
    rate_limiter: AdaptiveRateLimiter | None = None,
) -> ScrapeResult:
    """Compatibility wrapper: drain stream_dependents into a ScrapeResult.

    ``rows is None`` (default) returns every matched repo, sorted by stars, and
    ``max_pages`` defaults to ``MAX_PAGES_CEILING`` (1000) — the historical
    "return everything up to the ceiling" behavior, deliberately *overriding* the
    bounded 200-page (`DEFAULT_MAX_PAGES`) default of ``stream_dependents``. This
    keeps existing callers (the ``deps``/``search`` CLI in `app.py`, which pass no
    ``max_pages`` or their own) unchanged until Phase 4 introduces a user-facing
    ``--max-pages`` budget. Pass ``rows`` for a bounded top-K with adaptive stop.
    """
    terminal: ScrapeSnapshot | None = None
    async for snapshot in stream_dependents(
        session,
        url,
        rows=rows,
        dependent_type=dependent_type,
        min_stars=min_stars,
        cache=cache,
        on_progress=on_progress,
        token=token,
        max_pages=max_pages,
        concurrency=concurrency,
        adaptive_stop=adaptive_stop,
        rate_limiter=rate_limiter,
    ):
        terminal = snapshot

    assert terminal is not None  # noqa: S101  # stream always yields a terminal snapshot
    return ScrapeResult(
        repos=terminal.top_k,
        pages_scraped=terminal.pages_scraped,
        max_pages=min(max_pages, MAX_PAGES_CEILING),
        estimated_total_pages=terminal.estimated_total_pages,
        estimated_total_dependents=terminal.estimated_total_dependents,
        complete=terminal.complete,
        reason=terminal.reason,
        matched_count=terminal.matched_count,
    )
