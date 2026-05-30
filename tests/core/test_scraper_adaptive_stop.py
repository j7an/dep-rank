"""Tests for adaptive early-stopping (predicate + end-to-end termination)."""

from __future__ import annotations

import heapq
from collections import deque

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.models import Repository, ScrapeReason
from dep_rank.core.rate_limiter import AdaptiveRateLimiter
from dep_rank.core.scraper import (
    ADAPTIVE_W_MIN,
    ADAPTIVE_WINDOW,
    _should_stop,
    scrape_dependents,
)


def _fast_limiter() -> AdaptiveRateLimiter:
    """A non-throttling limiter for the long end-to-end walks below.

    These tests page through ``ADAPTIVE_W_MIN + ADAPTIVE_WINDOW + 5`` (~55) pages. The
    default unauthenticated limiter is 1/min, and even the authenticated 60/min bucket
    leaves only a ~5-token margin over this walk — fragile if the window constants grow.
    Inject a high-capacity bucket so ``acquire()`` never blocks regardless of page count.
    """
    return AdaptiveRateLimiter(rate=100_000, period=1.0, concurrency=3)


def _heap(stars: list[int]) -> list[tuple[int, int, Repository]]:
    """Build a *real* min-heap (not a plain list) so ``heap[0]`` is the smallest entry.

    ``_should_stop`` reads ``heap[0][0]`` as the K-th best (the heap minimum). A plain
    list comprehension in arbitrary order would leave ``heap[0]`` as the *first* element
    (e.g. the largest), so the predicate would compare the trailing window against the
    wrong threshold — silently mis-passing or mis-failing. ``heapq.heapify`` restores the
    invariant. (``count`` need not be negated here: these entries are never displaced via
    ``_heap_push``; only ``heap[0][0]`` — the stars — is inspected.)
    """
    h = [
        (s, i, Repository(owner="o", name=f"r{i}", url=f"https://github.com/o/r{i}", stars=s))
        for i, s in enumerate(stars)
    ]
    heapq.heapify(h)
    return h


class TestShouldStopPredicate:
    def test_converged_stops(self) -> None:
        heap = _heap([1000, 900, 800])  # kth_best (min) = 800
        recent = deque([10, 20, 5] * 7, maxlen=ADAPTIVE_WINDOW)  # all far below 800
        assert _should_stop(heap, rows=3, recent_max=recent, page=ADAPTIVE_W_MIN) is True

    def test_non_converged_continues(self) -> None:
        heap = _heap([1000, 900, 800])
        recent = deque([850] * ADAPTIVE_WINDOW, maxlen=ADAPTIVE_WINDOW)  # exceeds kth_best=800
        assert _should_stop(heap, rows=3, recent_max=recent, page=ADAPTIVE_W_MIN) is False

    def test_sparse_min_stars_never_saturates(self) -> None:
        heap = _heap([1000, 900])  # only 2 < rows=3 -> not saturated
        recent = deque([5] * ADAPTIVE_WINDOW, maxlen=ADAPTIVE_WINDOW)
        assert _should_stop(heap, rows=3, recent_max=recent, page=ADAPTIVE_W_MIN) is False

    def test_no_stop_before_w_min(self) -> None:
        heap = _heap([1000, 900, 800])
        recent = deque([5] * ADAPTIVE_WINDOW, maxlen=ADAPTIVE_WINDOW)
        assert _should_stop(heap, rows=3, recent_max=recent, page=ADAPTIVE_W_MIN - 1) is False

    def test_rows_none_never_stops(self) -> None:
        heap = _heap([1000, 900, 800])
        recent = deque([5] * ADAPTIVE_WINDOW, maxlen=ADAPTIVE_WINDOW)
        assert _should_stop(heap, rows=None, recent_max=recent, page=999) is False


# --- End-to-end: a decaying-star stream stops early with trend_converged ---

FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


def _item(name: str, stars: int) -> str:
    return (
        f'<div class="flex-items-center"><span>'
        f'<a class="text-bold" href="/o/{name}">o/{name}</a></span>'
        f"<div><span>{stars}</span></div></div>"
    )


def _decaying_page(page_num: int, total_pages: int) -> str:
    # Page 1 seeds three high-star repos; later pages are all low-star.
    if page_num == 1:
        body = _item("a", 9000) + _item("b", 8000) + _item("c", 7000)
    else:
        body = _item(f"low{page_num}", 10)
    nav = (
        f'<a href="/owner/repo/network/dependents?page={page_num + 1}">Next</a>'
        if page_num < total_pages
        else '<a href="/owner/repo/network/dependents?page=0">Previous</a>'
    )
    return f"""
    <html><body>
    <div class="table-list-header-toggle states flex-auto pl-0">
        <a class="btn-link selected"
           href="/owner/repo/network/dependents?dependent_type=REPOSITORY">3000 Repositories</a>
    </div>
    <div id="dependents"><div class="Box">{body}</div>
    <div class="paginate-container"><div>{nav}</div></div></div>
    </body></html>
    """


@pytest.mark.asyncio
async def test_decaying_stream_stops_with_trend_converged() -> None:
    total = ADAPTIVE_W_MIN + ADAPTIVE_WINDOW + 5  # enough pages to satisfy W_min + window
    with aioresponses() as m:
        m.get(FIRST, body=_decaying_page(1, total))
        for p in range(2, total + 1):
            m.get(
                f"https://github.com/owner/repo/network/dependents?page={p}",
                body=_decaying_page(p, total),
            )
        async with ClientSession() as session:
            result = await scrape_dependents(
                session,
                "https://github.com/owner/repo",
                rows=3,
                min_stars=5,
                max_pages=1000,
                rate_limiter=_fast_limiter(),
            )
    assert result.reason == ScrapeReason.TREND_CONVERGED
    assert result.complete is False
    assert result.pages_scraped < total  # stopped before exhausting
    assert [r.name for r in result.repos] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_no_adaptive_stop_runs_to_exhaustion() -> None:
    total = ADAPTIVE_W_MIN + ADAPTIVE_WINDOW + 5
    with aioresponses() as m:
        m.get(FIRST, body=_decaying_page(1, total))
        for p in range(2, total + 1):
            m.get(
                f"https://github.com/owner/repo/network/dependents?page={p}",
                body=_decaying_page(p, total),
            )
        async with ClientSession() as session:
            result = await scrape_dependents(
                session,
                "https://github.com/owner/repo",
                rows=3,
                min_stars=5,
                max_pages=1000,
                adaptive_stop=False,
                rate_limiter=_fast_limiter(),
            )
    assert result.complete is True
    assert result.reason is None
    assert result.pages_scraped == total
