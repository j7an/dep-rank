"""Tests for the streaming heap aggregator (top-K correctness + edge cases).

Multi-page tests pass ``token="ghp_x"`` so the per-scrape limiter is built via
``AdaptiveRateLimiter.for_token("ghp_x", ...)`` — the authenticated 60/min budget
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

from dep_rank.core.scraper import scrape_dependents, stream_dependents

BASE = "https://github.com/owner/repo"
FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


def _page(rows_html: str, next_page: int | None) -> str:
    nav = (
        f'<a href="/owner/repo/network/dependents?page={next_page}">Next</a>'
        if next_page is not None
        else '<a href="/owner/repo/network/dependents?page=0">Previous</a>'
    )
    return f"""
    <html><body>
    <div class="table-list-header-toggle states flex-auto pl-0">
        <a class="btn-link selected"
           href="/owner/repo/network/dependents?dependent_type=REPOSITORY">300 Repositories</a>
    </div>
    <div id="dependents"><div class="Box">{rows_html}</div>
    <div class="paginate-container"><div>{nav}</div></div></div>
    </body></html>
    """


def _item(owner: str, name: str, stars: int) -> str:
    return f"""
    <div class="flex-items-center">
        <span><a class="text-bold" href="/{owner}/{name}">{owner}/{name}</a></span>
        <div><span>{stars}</span></div>
    </div>
    """


@pytest.mark.asyncio
async def test_top_k_ordering_across_pages() -> None:
    """rows=2 keeps the two highest-star repos regardless of which page they were on."""
    p1 = _page(_item("a", "one", 100) + _item("b", "two", 5000), next_page=2)
    p2 = _page(_item("c", "three", 9000) + _item("d", "four", 200), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        m.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, BASE, rows=2, token="ghp_x", adaptive_stop=False
            )
    assert [r.name for r in result.repos] == ["three", "two"]
    assert result.repos[0].stars == 9000
    assert result.matched_count == 4  # all four passed min_stars=5
    assert result.complete is True


@pytest.mark.asyncio
async def test_rows_zero_keeps_no_repos_but_counts() -> None:
    p1 = _page(_item("a", "one", 100) + _item("b", "two", 50), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        async with ClientSession() as session:
            result = await scrape_dependents(session, BASE, rows=0)
    assert result.repos == []
    assert result.matched_count == 2


@pytest.mark.asyncio
async def test_rows_greater_than_total_returns_all() -> None:
    p1 = _page(_item("a", "one", 100) + _item("b", "two", 50), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        async with ClientSession() as session:
            result = await scrape_dependents(session, BASE, rows=50)
    assert [r.name for r in result.repos] == ["one", "two"]


@pytest.mark.asyncio
async def test_ties_keep_earlier_seen() -> None:
    """Equal stars: the repo seen first is retained when the heap is full."""
    p1 = _page(_item("a", "first", 500) + _item("b", "second", 500), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        async with ClientSession() as session:
            result = await scrape_dependents(session, BASE, rows=1)
    assert [r.name for r in result.repos] == ["first"]


@pytest.mark.asyncio
async def test_ties_evict_later_seen_when_displaced() -> None:
    """Regression for the eviction tiebreak (not just admission): when a
    higher-star repo displaces one of two equal-star repos in a full heap, the
    *later-seen* tie must be evicted and the earlier-seen one retained.

    Fails under a plain ``(stars, count, repo)`` entry (the min-heap root is the
    earliest-seen tie, so it gets evicted first) — only ``(stars, -count, repo)``
    keeps the earlier-seen one. ``test_ties_keep_earlier_seen`` above uses rows=1 and
    never evicts, so it passes under either ordering and cannot catch this.
    """
    p1 = _page(
        _item("a", "early", 500) + _item("b", "late", 500) + _item("c", "winner", 900),
        next_page=None,
    )
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        async with ClientSession() as session:
            result = await scrape_dependents(session, BASE, rows=2, adaptive_stop=False)
    assert [r.name for r in result.repos] == ["winner", "early"]


@pytest.mark.asyncio
async def test_duplicate_repos_counted_once() -> None:
    p1 = _page(_item("a", "dup", 100), next_page=2)
    p2 = _page(_item("a", "dup", 100) + _item("c", "new", 80), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        m.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, BASE, rows=10, token="ghp_x", adaptive_stop=False
            )
    assert result.matched_count == 2
    assert sorted(r.name for r in result.repos) == ["dup", "new"]


@pytest.mark.asyncio
async def test_max_pages_reached_sets_reason() -> None:
    """Hitting the page cap with more pages available -> complete=False, max_pages_reached."""
    from dep_rank.core.models import ScrapeReason

    p1 = _page(_item("a", "one", 100), next_page=2)
    p2 = _page(_item("b", "two", 80), next_page=3)  # page 2 still advertises a next page
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        m.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, BASE, rows=5, max_pages=2, token="ghp_x", adaptive_stop=False
            )
    assert result.pages_scraped == 2
    assert result.complete is False
    assert result.reason == ScrapeReason.MAX_PAGES_REACHED


@pytest.mark.asyncio
async def test_stream_emits_per_page_then_terminal() -> None:
    p1 = _page(_item("a", "one", 100), next_page=2)
    p2 = _page(_item("b", "two", 80), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        m.get("https://github.com/owner/repo/network/dependents?page=2", body=p2)
        async with ClientSession() as session:
            snaps = [
                s
                async for s in stream_dependents(
                    session, BASE, rows=5, token="ghp_x", adaptive_stop=False
                )
            ]
    assert [s.done for s in snaps] == [False, False, True]
    assert snaps[-1].complete is True
    assert snaps[-1].reason is None


@pytest.mark.asyncio
async def test_on_partial_called_per_snapshot() -> None:
    from dep_rank.core.models import ScrapeSnapshot

    seen: list[ScrapeSnapshot] = []

    async def on_partial(snap: ScrapeSnapshot) -> None:
        seen.append(snap)

    p1 = _page(_item("a", "one", 100) + _item("b", "two", 80), next_page=None)
    with aioresponses() as m:
        m.get(FIRST, body=p1)
        async with ClientSession() as session:
            await scrape_dependents(
                session, BASE, rows=5, on_partial=on_partial, adaptive_stop=False
            )
    # one per-page snapshot (done=False) + one terminal snapshot (done=True)
    assert len(seen) == 2
    assert seen[0].done is False
    assert seen[-1].done is True
