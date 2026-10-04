"""Tests for the streaming heap aggregator (top-K correctness + edge cases).

Multi-page tests pass ``token="ghp_x"`` so the per-scrape limiter is built via
``RateLimiter(60, 60.0)`` — the authenticated 60/min budget
(bucket capacity 60, starts full), so ``acquire()`` returns immediately for every page.
Without a token the limiter is the unauthenticated **1/min** bucket: page 1 drains the
lone token and page 2's ``acquire()`` does a real ``asyncio.sleep(60)``, hanging the
test. Single-page tests (``next_page=None``) make exactly one ``acquire()`` and so don't
need a token, but passing one is harmless.
"""

from __future__ import annotations

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.models import Repository, ScrapeSnapshot
from dep_rank.core.scraper import scrape_dependents
from tests.conftest import dependents_page

BASE = "https://github.com/owner/repo"
FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


async def test_top_k_ordering_across_pages(mock_http: aioresponses, session: ClientSession) -> None:
    """rows=2 keeps the two highest-star repos regardless of which page they were on."""
    p1 = dependents_page([("a", "one", 100), ("b", "two", 5000)], next_page=2, repos=300)
    p2 = dependents_page([("c", "three", 9000), ("d", "four", 200)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    mock_http.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
    result = await scrape_dependents(session, BASE, rows=2, token="ghp_x", adaptive_stop=False)
    assert [r.name for r in result.repos] == ["three", "two"]
    assert result.repos[0].stars == 9000
    assert result.matched_count == 4  # all four passed min_stars=5
    assert result.complete is True


async def test_rows_zero_keeps_no_repos_but_counts(
    mock_http: aioresponses, session: ClientSession
) -> None:
    p1 = dependents_page([("a", "one", 100), ("b", "two", 50)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    result = await scrape_dependents(session, BASE, rows=0)
    assert result.repos == []
    assert result.matched_count == 2


async def test_rows_greater_than_total_returns_all(
    mock_http: aioresponses, session: ClientSession
) -> None:
    p1 = dependents_page([("a", "one", 100), ("b", "two", 50)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    result = await scrape_dependents(session, BASE, rows=50)
    assert [r.name for r in result.repos] == ["one", "two"]


async def test_ties_keep_earlier_seen(mock_http: aioresponses, session: ClientSession) -> None:
    """Equal stars: the repo seen first is retained when the heap is full."""
    p1 = dependents_page([("a", "first", 500), ("b", "second", 500)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    result = await scrape_dependents(session, BASE, rows=1)
    assert [r.name for r in result.repos] == ["first"]


async def test_ties_evict_later_seen_when_displaced(
    mock_http: aioresponses, session: ClientSession
) -> None:
    """Regression for the eviction tiebreak (not just admission): when a
    higher-star repo displaces one of two equal-star repos in a full heap, the
    *later-seen* tie must be evicted and the earlier-seen one retained.

    Fails under a plain ``(stars, count, repo)`` entry (the min-heap root is the
    earliest-seen tie, so it gets evicted first) — only ``(stars, -count, repo)``
    keeps the earlier-seen one. ``test_ties_keep_earlier_seen`` above uses rows=1 and
    never evicts, so it passes under either ordering and cannot catch this.
    """
    p1 = dependents_page([("a", "early", 500), ("b", "late", 500), ("c", "winner", 900)], repos=300)
    mock_http.get(FIRST, body=p1)
    result = await scrape_dependents(session, BASE, rows=2, adaptive_stop=False)
    assert [r.name for r in result.repos] == ["winner", "early"]


async def test_duplicate_repos_counted_once(
    mock_http: aioresponses, session: ClientSession
) -> None:
    p1 = dependents_page([("a", "dup", 100)], next_page=2, repos=300)
    p2 = dependents_page([("a", "dup", 100), ("c", "new", 80)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    mock_http.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
    result = await scrape_dependents(session, BASE, rows=10, token="ghp_x", adaptive_stop=False)
    assert result.matched_count == 2
    assert sorted(r.name for r in result.repos) == ["dup", "new"]


async def test_max_pages_reached_sets_reason(
    mock_http: aioresponses, session: ClientSession
) -> None:
    """Hitting the page cap with more pages available -> complete=False, max_pages_reached."""
    from dep_rank.core.models import ScrapeReason

    p1 = dependents_page([("a", "one", 100)], next_page=2, repos=300)
    p2 = dependents_page(
        [("b", "two", 80)], next_page=3, repos=300
    )  # page 2 still advertises a next page
    mock_http.get(FIRST, body=p1)
    mock_http.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
    result = await scrape_dependents(
        session, BASE, rows=5, max_pages=2, token="ghp_x", adaptive_stop=False
    )
    assert result.pages_scraped == 2
    assert result.complete is False
    assert result.reason == ScrapeReason.MAX_PAGES_REACHED


async def test_on_page_fires_per_page_and_result_carries_outcome(
    mock_http: aioresponses, session: ClientSession
) -> None:
    seen: list[int] = []

    async def on_page(snap: ScrapeSnapshot) -> None:
        seen.append(snap.pages_scraped)

    p1 = dependents_page([("a", "one", 100)], next_page=2, repos=300)
    p2 = dependents_page([("b", "two", 80)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    mock_http.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
    result = await scrape_dependents(
        session, BASE, rows=5, token="ghp_x", adaptive_stop=False, on_page=on_page
    )
    assert seen == [1, 2]
    assert result.complete is True and result.reason is None


async def test_rows_zero_walks_pages_and_reports_matches(
    mock_http: aioresponses, session: ClientSession
) -> None:
    tops: list[list[Repository]] = []

    async def on_page(snap: ScrapeSnapshot) -> None:
        tops.append(snap.top_k)

    p1 = dependents_page([("a", "one", 100), ("b", "two", 50)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    result = await scrape_dependents(session, BASE, rows=0, on_page=on_page)
    assert result.repos == []
    assert result.matched_count == 2
    assert tops == [[]]


async def test_on_page_exception_propagates(
    mock_http: aioresponses, session: ClientSession
) -> None:
    async def on_page(snap: ScrapeSnapshot) -> None:
        raise RuntimeError("render failed")

    p1 = dependents_page([("a", "one", 100)], next_page=None, repos=300)
    mock_http.get(FIRST, body=p1)
    with pytest.raises(RuntimeError, match="render failed"):
        await scrape_dependents(session, BASE, rows=5, on_page=on_page)
