"""Tests for GitHub dependents HTML scraper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.models import (
    DependentType,
    Repository,
    ScrapeReason,
    ScrapeResult,
    ScrapeSnapshot,
)
from dep_rank.core.rate_limiter import (
    AUTH_RATE,
    RATE_PERIOD,
    RETRY_MAX_SECONDS,
    UNAUTH_RATE,
    RateLimiter,
)
from dep_rank.core.scraper import (
    DEFAULT_MAX_PAGES,
    MAX_RETRIES,
    parse_dependent_counts,
    parse_dependents_page,
    scrape_dependents,
)
from tests.conftest import (
    DEPENDENTS_HTML_LAST_PAGE,
    DEPENDENTS_HTML_NO_RESULTS,
    DEPENDENTS_HTML_PAGE_1,
    DEPENDENTS_HTML_WITH_COUNTS,
    DEPENDENTS_HTML_WITH_COUNTS_PAGE_1,
)

FIRST_URL = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


def _fast_limiter() -> RateLimiter:
    """A non-throttling limiter for multi-page tests not about rate limiting.

    A token-less scrape builds the unauthenticated 1/min limiter, whose lone token is
    drained by page 1; page 2's ``acquire()`` would then ``asyncio.sleep(~60)``. These
    tests exercise pagination/filtering/progress/estimates, not throttling, so inject a
    high-capacity bucket that never blocks.
    """
    return RateLimiter(rate=100_000, period=1.0)


class TestParseDependentCounts:
    def test_parse_both_counts(self) -> None:
        html = """
        <html><body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                2,295,450
                Repositories
            </a>
            <a class="btn-link " href="/owner/repo/network/dependents?dependent_type=PACKAGE">
                44,317
                Packages
            </a>
        </div>
        </body></html>
        """
        counts = parse_dependent_counts(html)
        assert counts == {"REPOSITORY": 2295450, "PACKAGE": 44317}

    def test_parse_single_count(self) -> None:
        html = """
        <html><body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected" href="?dependent_type=REPOSITORY">
                500
                Repositories
            </a>
        </div>
        </body></html>
        """
        counts = parse_dependent_counts(html)
        assert counts == {"REPOSITORY": 500}

    def test_parse_missing_structure(self) -> None:
        html = "<html><body><p>No dependents info</p></body></html>"
        counts = parse_dependent_counts(html)
        assert counts == {}

    def test_parse_non_numeric(self) -> None:
        html = """
        <html><body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected" href="?dependent_type=REPOSITORY">
                NaN
                Repositories
            </a>
        </div>
        </body></html>
        """
        counts = parse_dependent_counts(html)
        assert counts == {}

    def test_parse_singular_forms(self) -> None:
        html = """
        <html><body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected" href="?dependent_type=REPOSITORY">
                1
                Repository
            </a>
            <a class="btn-link " href="?dependent_type=PACKAGE">
                1
                Package
            </a>
        </div>
        </body></html>
        """
        counts = parse_dependent_counts(html)
        assert counts == {"REPOSITORY": 1, "PACKAGE": 1}


class TestParseDependentsPage:
    def test_parse_repos(self) -> None:
        repos, next_url = parse_dependents_page(DEPENDENTS_HTML_PAGE_1)
        assert len(repos) == 3
        assert repos[0] == Repository(
            owner="alpha",
            name="framework",
            url="https://github.com/alpha/framework",
            stars=12500,
        )
        assert repos[1].stars == 3200
        assert repos[2].stars == 150

    @pytest.mark.parametrize(
        "href",
        ["/o/r/network/dependents?page=2", "https://github.com/o/r/network/dependents?page=2"],
    )
    def test_next_url_from_relative_or_absolute_href(self, href: str) -> None:
        html = (
            '<div id="dependents"><div class="paginate-container"><div>'
            f'<a href="{href}">Next</a></div></div></div>'
        )
        assert parse_dependents_page(html)[1] == "https://github.com/o/r/network/dependents?page=2"

    def test_next_url_is_second_of_two_links(self) -> None:
        html = (
            '<div id="dependents"><div class="paginate-container"><div>'
            '<a href="/o/r/network/dependents?page=1">Previous</a>'
            '<a href="/o/r/network/dependents?page=3">Next</a></div></div></div>'
        )
        assert parse_dependents_page(html)[1] == "https://github.com/o/r/network/dependents?page=3"

    @pytest.mark.parametrize(
        "href",
        [
            "//evil.example/o/r/network/dependents?page=2",
            "https://evil.example/o/r/network/dependents?page=2",
            "http://github.com/o/r/network/dependents?page=2",
        ],
    )
    def test_next_link_off_github_https_is_ignored(self, href: str) -> None:
        html = (
            '<div id="dependents"><div class="paginate-container"><div>'
            f'<a href="{href}">Next</a></div></div></div>'
        )
        assert parse_dependents_page(html)[1] is None

    def test_parse_next_url(self) -> None:
        _, next_url = parse_dependents_page(DEPENDENTS_HTML_PAGE_1)
        assert next_url is not None
        assert "page=2" in next_url

    def test_parse_last_page_no_next(self) -> None:
        _, next_url = parse_dependents_page(DEPENDENTS_HTML_LAST_PAGE)
        assert next_url is None

    def test_parse_no_results(self) -> None:
        repos, next_url = parse_dependents_page(DEPENDENTS_HTML_NO_RESULTS)
        assert len(repos) == 0
        assert next_url is None


class TestScrapeDependents:
    async def test_single_page(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_LAST_PAGE,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
            assert len(result.repos) == 1
            assert result.repos[0].owner == "delta"

    async def test_pagination(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_PAGE_1,
            )
            m.get(
                "https://github.com/owner/repo/network/dependents?page=2",
                body=DEPENDENTS_HTML_LAST_PAGE,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    rate_limiter=_fast_limiter(),
                    rows=100,
                )
            assert len(result.repos) == 4

    async def test_min_stars_filter(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_PAGE_1,
            )
            m.get(
                "https://github.com/owner/repo/network/dependents?page=2",
                body=DEPENDENTS_HTML_LAST_PAGE,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    min_stars=200,
                    rate_limiter=_fast_limiter(),
                    rows=100,
                )
            assert all(r.stars >= 200 for r in result.repos)

    async def test_deduplication(self) -> None:
        html_with_dupe = DEPENDENTS_HTML_PAGE_1.replace("gamma/utils", "alpha/framework").replace(
            "150", "12,500"
        )
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=html_with_dupe,
            )
            m.get(
                "https://github.com/owner/repo/network/dependents?page=2",
                body=DEPENDENTS_HTML_LAST_PAGE,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    rate_limiter=_fast_limiter(),
                    rows=100,
                )
            urls = [r.url for r in result.repos]
            assert len(urls) == len(set(urls))

    async def test_package_type(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=PACKAGE",
                body=DEPENDENTS_HTML_LAST_PAGE,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    dependent_type=DependentType.PACKAGE,
                    rows=100,
                )
            assert len(result.repos) == 1

    async def test_complete_scrape_sets_complete_true(self) -> None:
        """A scrape that exhausts all pages reports complete=True, reason=None,
        and matched_count equal to the number of repos that passed min_stars."""
        async with ClientSession() as session:
            with aioresponses() as m:
                m.get(
                    "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                    body=DEPENDENTS_HTML_LAST_PAGE,
                )
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    min_stars=0,
                    token="ghp_x",
                    rows=100,
                )
        assert result.complete is True
        assert result.reason is None
        assert result.matched_count == len(result.repos)

    async def test_cap_sets_max_pages_reached(self) -> None:
        """Stopping at the page cap with a remaining next-page link reports
        complete=False, reason=MAX_PAGES_REACHED."""
        async with ClientSession() as session:
            with aioresponses() as m:
                m.get(
                    "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                    body=DEPENDENTS_HTML_PAGE_1,
                )
                # Registered only to give page 1 a valid next-page link; with
                # max_pages=1 the walk caps before page 2 is ever fetched.
                m.get(
                    "https://github.com/owner/repo/network/dependents?page=2",
                    body=DEPENDENTS_HTML_LAST_PAGE,
                )
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    min_stars=0,
                    token="ghp_x",
                    max_pages=1,
                    rows=100,
                )
        assert result.pages_scraped == 1
        assert result.complete is False
        assert result.reason == ScrapeReason.MAX_PAGES_REACHED

    async def test_fetch_failure_sets_network_failure(self) -> None:
        """A fetch that fails with an unexpected status (raising
        ``NetworkFailureError``) reports complete=False, reason=NETWORK_FAILURE."""
        async with ClientSession() as session:
            with aioresponses() as m:
                m.get(
                    "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                    status=500,
                )
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    min_stars=0,
                    token="ghp_x",
                    rows=100,
                )
        assert result.complete is False
        assert result.reason == ScrapeReason.NETWORK_FAILURE
        assert result.pages_scraped == 0  # a failed first fetch consumed zero pages


class TestScrapeDependentsEdgeCases:
    def test_parse_malformed_html_no_repo_link(self) -> None:
        """Items without a repo link are skipped."""
        html = """
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="other-class" href="/foo/bar">foo/bar</a></span>
                <div><span>100</span></div>
            </div>
        </div></div>
        </body></html>
        """
        repos, next_url = parse_dependents_page(html)
        assert len(repos) == 0

    def test_parse_missing_stars(self) -> None:
        """Items where stars text is not numeric are skipped."""
        html = """
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="text-bold" href="/foo/bar">foo/bar</a></span>
                <div><div><span>not-a-number</span></div></div>
            </div>
        </div></div>
        </body></html>
        """
        repos, next_url = parse_dependents_page(html)
        assert len(repos) == 0

    def test_parse_empty_href(self) -> None:
        """Items with empty href are skipped."""
        html = """
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="text-bold" href="">foo/bar</a></span>
                <div><div><span>100</span></div></div>
            </div>
        </div></div>
        </body></html>
        """
        repos, next_url = parse_dependents_page(html)
        assert len(repos) == 0

    def test_parse_invalid_path_segments(self) -> None:
        """Items where href has wrong number of segments are skipped."""
        html = """
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="text-bold" href="/only-one">only-one</a></span>
                <div><div><span>100</span></div></div>
            </div>
        </div></div>
        </body></html>
        """
        repos, next_url = parse_dependents_page(html)
        assert len(repos) == 0

    async def test_error_response_sets_network_failure(self) -> None:
        """A non-200/304/429 response terminates with reason=network_failure."""
        from dep_rank.core.models import ScrapeReason

        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                status=500,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert result.repos == []
        assert result.complete is False
        assert result.reason == ScrapeReason.NETWORK_FAILURE
        assert result.pages_scraped == 0  # first-page failure consumed no pages

    async def test_429_exhaustion_sets_rate_limited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A persistent 429 (past the retry budget) terminates with reason=rate_limited."""
        from unittest.mock import AsyncMock

        from dep_rank.core.models import ScrapeReason

        # Patch the scraper's sleep so the (growing) 429 backoff does not actually wait.
        monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())

        url = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
        with aioresponses() as m:
            for _ in range(6):  # MAX_RETRIES + 1 attempts, all 429
                m.get(url, status=429, headers={"Retry-After": "0"})
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    token="ghp_x",
                    rows=100,
                )
        assert result.complete is False
        assert result.reason == ScrapeReason.RATE_LIMITED
        assert result.pages_scraped == 0  # never got a parseable page

    async def test_cache_hit_skips_network(self) -> None:
        """When cache has a valid (non-expired) entry, no network request is made."""
        import tempfile

        from dep_rank.core.cache import SqliteCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SqliteCache(tmpdir)
            await cache.initialize()
            await cache.put(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                DEPENDENTS_HTML_LAST_PAGE.encode("utf-8"),
                etag='"etag1"',
                ttl=3600,
            )
            # No aioresponses mock needed — if it tries to fetch, it will fail
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    cache=cache,
                    rows=100,
                )
            await cache.close()
        assert len(result.repos) == 1
        assert result.repos[0].owner == "delta"

    async def test_200_response_stores_in_cache(self) -> None:
        """200 response with cache stores the body and etag."""
        import tempfile

        from dep_rank.core.cache import SqliteCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SqliteCache(tmpdir)
            await cache.initialize()

            with aioresponses() as m:
                m.get(
                    "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                    body=DEPENDENTS_HTML_LAST_PAGE,
                    headers={"ETag": '"new-etag"'},
                )
                async with ClientSession() as session:
                    await scrape_dependents(
                        session, "https://github.com/owner/repo", cache=cache, rows=100
                    )

            # Verify cache was populated
            cached = await cache.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
            )
            assert cached is not None
            assert cached["etag"] == '"new-etag"'
            await cache.close()


class TestScrapeResultReturn:
    async def test_returns_scrape_result(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_WITH_COUNTS,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert isinstance(result, ScrapeResult)
        assert result.pages_scraped == 1
        assert result.max_pages == DEFAULT_MAX_PAGES
        assert result.estimated_total_pages == 900 // 30  # 30
        assert result.estimated_total_dependents == 900
        assert len(result.repos) == 1
        assert result.repos[0].owner == "alpha"

    async def test_estimated_total_pages_with_max_pages(self) -> None:
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_WITH_COUNTS,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    max_pages=5,
                    rows=100,
                )
        assert result.max_pages == 5

    async def test_no_counts_in_html_defaults_to_zero(self) -> None:
        """When the HTML has no parseable count header, estimated totals default to 0."""
        html_no_counts = """
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="text-bold" href="/delta/app">delta/app</a></span>
                <div><div><span>80</span></div></div>
            </div>
        </div>
        <div class="paginate-container"><div>
            <a href="/owner/repo/network/dependents?page=1">Previous</a>
        </div></div>
        </div>
        </body></html>
        """
        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=html_no_counts,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert result.estimated_total_pages == 0
        assert result.estimated_total_dependents == 0

    async def test_multi_page_with_estimated_total(self) -> None:
        """Multi-page scrape carries estimated_total_pages from page 1 through all callbacks."""
        seen: list[ScrapeSnapshot] = []

        async def on_page(snap: ScrapeSnapshot) -> None:
            seen.append(snap)

        with aioresponses() as m:
            m.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_WITH_COUNTS_PAGE_1,
            )
            m.get(
                "https://github.com/owner/repo/network/dependents?page=2",
                body=DEPENDENTS_HTML_WITH_COUNTS,
            )
            async with ClientSession() as session:
                result = await scrape_dependents(
                    session,
                    "https://github.com/owner/repo",
                    on_page=on_page,
                    rate_limiter=_fast_limiter(),
                    rows=100,
                )
        assert result.pages_scraped == 2
        assert result.estimated_total_pages == 30  # 900 // 30
        assert result.estimated_total_dependents == 900
        # Both pages get the same estimated total (parsed from page 1)
        assert [(s.pages_scraped, s.estimated_total_pages) for s in seen] == [(1, 30), (2, 30)]


@pytest.mark.parametrize(("token", "rate"), [(None, UNAUTH_RATE), ("ghp_x", AUTH_RATE)])
async def test_limiter_budget_follows_token(
    monkeypatch: pytest.MonkeyPatch, token: str | None, rate: int
) -> None:
    built: list[tuple[int, float]] = []
    real = RateLimiter

    def record(r: int, p: float) -> RateLimiter:
        built.append((r, p))
        return real(100_000, 1.0)

    monkeypatch.setattr("dep_rank.core.scraper.RateLimiter", record)
    first_url = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
    with aioresponses() as m:
        m.get(first_url, body=DEPENDENTS_HTML_LAST_PAGE)
        async with ClientSession() as session:
            await scrape_dependents(session, "https://github.com/owner/repo", token=token, rows=100)
    assert built == [(rate, RATE_PERIOD)]


async def test_http_date_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    with aioresponses() as m:
        m.get(FIRST_URL, status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        m.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session,
                "https://github.com/owner/repo",
                token="ghp_x",
                rows=100,
            )
    assert result.complete is True
    assert [r.owner for r in result.repos] == ["delta"]


async def test_infinite_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", sleep)
    with aioresponses() as m:
        m.get(FIRST_URL, status=429, headers={"Retry-After": "inf"})
        m.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, "https://github.com/owner/repo", rows=100, token="ghp_x"
            )
    assert result.complete is True
    delays = [call.args[0] for call in sleep.await_args_list]
    assert delays
    assert all(0 <= d <= RETRY_MAX_SECONDS for d in delays)


async def test_transport_errors_exhaust_to_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    with aioresponses() as m:
        for _ in range(MAX_RETRIES + 1):
            m.get(FIRST_URL, exception=aiohttp.ClientConnectionError())
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, "https://github.com/owner/repo", rows=100, token="ghp_x"
            )
    assert result.complete is False
    assert result.reason == ScrapeReason.NETWORK_FAILURE
    assert result.pages_scraped == 0


async def test_transport_error_then_success_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    with aioresponses() as m:
        m.get(FIRST_URL, exception=aiohttp.ClientConnectionError())
        m.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        async with ClientSession() as session:
            result = await scrape_dependents(
                session, "https://github.com/owner/repo", rows=100, token="ghp_x"
            )
    assert result.complete is True
    assert [r.owner for r in result.repos] == ["delta"]
