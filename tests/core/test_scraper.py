"""Tests for GitHub dependents HTML scraper."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock

import httpx2
import pytest

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
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    UNAUTH_RATE,
    RateLimiter,
)
from dep_rank.core.scraper import (
    DEFAULT_MAX_PAGES,
    MAX_RETRIES,
    NotFoundError,
    PackageOption,
    format_names,
    parse_dependent_counts,
    parse_dependents_page,
    parse_packages,
    scrape_dependents,
)
from tests.conftest import (
    DEPENDENTS_HTML_EMPTY,
    DEPENDENTS_HTML_LAST_PAGE,
    DEPENDENTS_HTML_NO_HEADER,
    DEPENDENTS_HTML_NO_RESULTS,
    DEPENDENTS_HTML_PAGE_1,
    DEPENDENTS_HTML_WITH_COUNTS,
    DEPENDENTS_HTML_WITH_COUNTS_PAGE_1,
    FakeHTTP,
    StalledBody,
    dependents_page,
    fast_limiter,
)

FIRST_URL = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


def _header(*links: tuple[str, str]) -> str:
    """A dependents-page count header with one ``count``/``label`` link per tuple."""
    anchors = "".join(
        f'<a class="btn-link" href="?dependent_type=X">\n    {count}\n    {label}\n</a>'
        for count, label in links
    )
    return f'<div class="table-list-header-toggle states flex-auto pl-0">{anchors}</div>'


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            _header(("2,295,450", "Repositories"), ("44,317", "Packages")),
            {"REPOSITORY": 2295450, "PACKAGE": 44317},
            id="both-counts",
        ),
        pytest.param(_header(("500", "Repositories")), {"REPOSITORY": 500}, id="single-count"),
        pytest.param("<p>No dependents info</p>", {}, id="missing-structure"),
        pytest.param(_header(("NaN", "Repositories")), {}, id="non-numeric"),
        pytest.param(
            _header(("1", "Repository"), ("1", "Package")),
            {"REPOSITORY": 1, "PACKAGE": 1},
            id="singular-forms",
        ),
    ],
)
def test_parse_dependent_counts(html: str, expected: dict[str, int]) -> None:
    assert parse_dependent_counts(f"<html><body>{html}</body></html>") == expected


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

    def test_parse_last_page_no_next(self) -> None:
        _, next_url = parse_dependents_page(DEPENDENTS_HTML_LAST_PAGE)
        assert next_url is None

    def test_parse_no_results(self) -> None:
        repos, next_url = parse_dependents_page(DEPENDENTS_HTML_NO_RESULTS)
        assert len(repos) == 0
        assert next_url is None


class TestScrapeDependents:
    async def test_min_stars_filter(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_PAGE_1,
        )
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?page=2",
            body=DEPENDENTS_HTML_LAST_PAGE,
        )
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            min_stars=200,
            rate_limiter=fast_limiter(),
            rows=100,
        )
        assert all(r.stars >= 200 for r in result.repos)

    async def test_package_type(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=PACKAGE",
            body=DEPENDENTS_HTML_LAST_PAGE,
        )
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            dependent_type=DependentType.PACKAGE,
            rows=100,
        )
        assert len(result.repos) == 1


class TestScrapeDependentsEdgeCases:
    @pytest.mark.parametrize(
        ("anchor_class", "href", "stars", "expected"),
        [
            pytest.param("text-bold", "/foo/bar", "100", 1, id="valid-row-control"),
            pytest.param("other-class", "/foo/bar", "100", 0, id="no-repo-link"),
            pytest.param("text-bold", "/foo/bar", "not-a-number", 0, id="non-numeric-stars"),
            pytest.param("text-bold", "", "100", 0, id="empty-href"),
            pytest.param("text-bold", "/only-one", "100", 0, id="one-path-segment"),
        ],
    )
    def test_parse_skips_malformed_rows(
        self, anchor_class: str, href: str, stars: str, expected: int
    ) -> None:
        html = f"""
        <html><body>
        <div id="dependents"><div class="Box">
            <div class="flex-items-center">
                <span><a class="{anchor_class}" href="{href}">foo/bar</a></span>
                <div><div><span>{stars}</span></div></div>
            </div>
        </div></div>
        </body></html>
        """
        repos, _ = parse_dependents_page(html)
        assert len(repos) == expected

    async def test_error_response_sets_network_failure(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """A non-200/304/429 response terminates with reason=network_failure."""

        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            status=500,
        )
        result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert result.repos == []
        assert result.complete is False
        assert result.reason == ScrapeReason.NETWORK_FAILURE
        assert result.pages_scraped == 0  # first-page failure consumed no pages

    async def test_cache_hit_skips_network(
        self, cache: SqliteCache, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """When cache has a valid (non-expired) entry, no network request is made."""
        await cache.put(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            DEPENDENTS_HTML_LAST_PAGE.encode("utf-8"),
            etag='"etag1"',
            ttl=3600,
        )
        # Not a RequestError, so a regression fails at once instead of retrying with backoff.
        mock_http.get(FIRST_URL, exception=AssertionError("unexpected fetch"))
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            cache=cache,
            rows=100,
        )
        assert mock_http.request_count == 0
        assert len(result.repos) == 1
        assert result.repos[0].owner == "delta"
        assert result.stale_pages == 0  # a fresh hit is not stale

    async def test_200_response_stores_in_cache(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """200 response with cache stores the body and etag."""

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SqliteCache(tmpdir)
            await cache.initialize()

            mock_http.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
                body=DEPENDENTS_HTML_LAST_PAGE,
                headers={"ETag": '"new-etag"'},
            )
            await scrape_dependents(session, "https://github.com/owner/repo", cache=cache, rows=100)

            # Verify cache was populated
            cached = await cache.get(
                "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
            )
            assert cached is not None
            assert cached["etag"] == '"new-etag"'
            await cache.close()


class TestScrapeResultReturn:
    async def test_returns_scrape_result(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_WITH_COUNTS,
        )
        result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert isinstance(result, ScrapeResult)
        assert result.pages_scraped == 1
        assert result.max_pages == DEFAULT_MAX_PAGES
        assert result.estimated_total_pages == 900 // 30  # 30
        assert result.estimated_total_dependents == 900
        assert len(result.repos) == 1
        assert result.repos[0].owner == "alpha"

    async def test_estimated_total_pages_with_max_pages(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_WITH_COUNTS,
        )
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            max_pages=5,
            rows=100,
        )
        assert result.max_pages == 5

    async def test_no_counts_in_html_leaves_total_unknown(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """An unparseable count header is unknown (None), never a reported 0."""
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_NO_HEADER,
        )
        result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert result.estimated_total_pages == 0
        assert result.estimated_total_dependents is None
        assert len(result.repos) == 1

    async def test_empty_state_reports_zero(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """GitHub's 0/0 empty state is a complete scrape with a parsed total of 0."""
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_EMPTY,
        )
        result = await scrape_dependents(session, "https://github.com/owner/repo", rows=100)
        assert result.estimated_total_dependents == 0
        assert result.complete is True
        assert result.repos == []

    async def test_multi_page_with_estimated_total(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """Multi-page scrape carries estimated_total_pages from page 1 through all callbacks."""
        seen: list[ScrapeSnapshot] = []

        async def on_page(snap: ScrapeSnapshot) -> None:
            seen.append(snap)

        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_WITH_COUNTS_PAGE_1,
        )
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?page=2",
            body=DEPENDENTS_HTML_WITH_COUNTS,
        )
        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            on_page=on_page,
            rate_limiter=fast_limiter(),
            rows=100,
        )
        assert result.pages_scraped == 2
        assert result.estimated_total_pages == 30  # 900 // 30
        assert result.estimated_total_dependents == 900
        # Both pages get the same estimated total (parsed from page 1)
        assert [(s.pages_scraped, s.estimated_total_pages) for s in seen] == [(1, 30), (2, 30)]


@pytest.mark.parametrize(("token", "rate"), [(None, UNAUTH_RATE), ("ghp_x", AUTH_RATE)])
async def test_limiter_budget_follows_token(
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
    rate: int,
    mock_http: FakeHTTP,
    session: httpx2.AsyncClient,
) -> None:
    built: list[tuple[int, float]] = []
    real = RateLimiter

    def record(r: int, p: float) -> RateLimiter:
        built.append((r, p))
        return real(100_000, 1.0)

    monkeypatch.setattr("dep_rank.core.scraper.RateLimiter", record)
    first_url = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
    mock_http.get(first_url, body=DEPENDENTS_HTML_LAST_PAGE)
    await scrape_dependents(session, "https://github.com/owner/repo", token=token, rows=100)
    assert built == [(rate, RATE_PERIOD)]


async def test_http_date_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", sleep)
    monkeypatch.setattr("dep_rank.core.rate_limiter.random.uniform", lambda a, b: b)
    mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
    result = await scrape_dependents(
        session,
        "https://github.com/owner/repo",
        token="ghp_x",
        rows=100,
    )
    assert result.complete is True
    assert [r.owner for r in result.repos] == ["delta"]
    # The date form is not parsed as seconds, so the delay is the plain backoff.
    delays = [c.args[0] for c in sleep.await_args_list if c.args and c.args[0] > 0]
    assert delays == [RETRY_BASE_SECONDS]


async def test_backoff_resets_after_success(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", sleep)
    monkeypatch.setattr("dep_rank.core.rate_limiter.random.uniform", lambda a, b: b)
    page_2 = "https://github.com/owner/repo/network/dependents?page=2"
    mock_http.get(FIRST_URL, status=429)
    mock_http.get(FIRST_URL, body=dependents_page([("a", "one", 10)], next_page=2))
    mock_http.get(page_2, status=429)
    mock_http.get(page_2, body=dependents_page([("b", "two", 5)]))
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.complete is True
    # Page 1's success resets backoff growth, so page 2's 429 waits the base delay again.
    delays = [c.args[0] for c in sleep.await_args_list if c.args and c.args[0] > 0]
    assert delays == [RETRY_BASE_SECONDS, RETRY_BASE_SECONDS]


async def test_infinite_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", sleep)
    mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "inf"})
    mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.complete is True
    delays = [call.args[0] for call in sleep.await_args_list]
    assert delays
    assert all(0 <= d <= RETRY_MAX_SECONDS for d in delays)


async def test_transport_errors_exhaust_to_network_failure(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    for _ in range(MAX_RETRIES + 1):
        mock_http.get(FIRST_URL, exception=httpx2.ConnectError("refused"))
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.complete is False
    assert result.reason == ScrapeReason.NETWORK_FAILURE
    assert result.pages_scraped == 0


async def test_missing_repository_raises_not_found(
    mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    mock_http.get(FIRST_URL, status=404)
    with pytest.raises(NotFoundError, match=r"^repository not found \(or private\): owner/repo$"):
        await scrape_dependents(
            session, "https://github.com/owner/repo", rows=10, rate_limiter=fast_limiter()
        )
    assert mock_http.request_count == 1  # 404 is not retried


async def test_later_page_404_stays_network_failure(
    mock_http: FakeHTTP, session: httpx2.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_PAGE_1)
    mock_http.get("https://github.com/owner/repo/network/dependents?page=2", status=404)
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=10, rate_limiter=fast_limiter()
    )
    assert result.reason == ScrapeReason.NETWORK_FAILURE
    assert result.pages_scraped == 1
    assert [r.owner for r in result.repos] == ["alpha", "beta", "gamma"]
    assert "HTTP 404 on page 2" in caplog.text  # the cause stays visible on stderr


async def test_transport_error_then_success_completes(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    mock_http.get(FIRST_URL, exception=httpx2.ConnectError("refused"))
    mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.complete is True
    assert [r.owner for r in result.repos] == ["delta"]


async def test_read_timeout_is_retried(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
    mock_http.get(FIRST_URL, exception=httpx2.ReadTimeout("slow"))
    mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.complete is True
    assert [r.owner for r in result.repos] == ["delta"]


async def test_rate_limited_with_stalled_body_stays_rate_limited(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())

    async def stalled_429(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, headers={"Retry-After": "1"}, stream=StalledBody())

    for _ in range(MAX_RETRIES + 1):
        mock_http.get(FIRST_URL, callback=stalled_429)
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=100, token="ghp_x"
    )
    assert result.reason == ScrapeReason.RATE_LIMITED


class TestRetryReporting:
    PAGE2_URL = "https://github.com/owner/repo/network/dependents?page=2"

    async def _scrape(
        self, session: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[ScrapeResult, list[RetryStatus], AsyncMock]:
        sleep = AsyncMock()
        monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", sleep)
        seen: list[RetryStatus] = []

        async def on_retry(status: RetryStatus) -> None:
            seen.append(status)

        result = await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            token="ghp_x",
            rows=100,
            rate_limiter=fast_limiter(),
            on_retry=on_retry,
        )
        return result, seen, sleep

    async def test_retry_count_is_per_page(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_http: FakeHTTP,
        session: httpx2.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One 429 on each of two pages reports retry 1 for page 1, then page 2."""
        mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "120"})
        mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_PAGE_1)
        mock_http.get(self.PAGE2_URL, status=429, headers={"Retry-After": "120"})
        mock_http.get(self.PAGE2_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        result, seen, _ = await self._scrape(session, monkeypatch)
        assert result.complete is True
        assert [(s.page, s.attempt, s.max_retries) for s in seen] == [
            (1, 1, MAX_RETRIES),
            (2, 1, MAX_RETRIES),
        ]
        assert all(s.delay == 120 for s in seen)  # Retry-After dominates first backoff
        # The callback replaces the warning line, so a live display is not duplicated.
        assert "Rate limited" not in caplog.text

    async def test_retry_count_advances_on_same_page(
        self, monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        for _ in range(2):
            mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "0"})
        mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        _, seen, _ = await self._scrape(session, monkeypatch)
        assert [(s.page, s.attempt) for s in seen] == [(1, 1), (1, 2)]

    async def test_exhaustion_reports_only_real_retries(
        self, monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """The final 429 gives up at once: no "6/5" report and no wasted sleep."""
        for _ in range(MAX_RETRIES + 1):
            mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "0"})
        result, seen, sleep = await self._scrape(session, monkeypatch)
        assert result.reason == ScrapeReason.RATE_LIMITED
        assert [s.attempt for s in seen] == list(range(1, MAX_RETRIES + 1))
        assert sleep.await_count == MAX_RETRIES

    async def test_warning_logged_without_callback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_http: FakeHTTP,
        session: httpx2.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
        mock_http.get(FIRST_URL, status=429, headers={"Retry-After": "0"})
        mock_http.get(FIRST_URL, body=DEPENDENTS_HTML_LAST_PAGE)
        await scrape_dependents(
            session, "https://github.com/owner/repo", rows=100, rate_limiter=fast_limiter()
        )
        assert "Rate limited on page 1" in caplog.text
        assert f"(1/{MAX_RETRIES})" in caplog.text


MENU = [("pkg/default", "ID0", True), ("pkg/other", "ID1", False), ("pkg/third", "ID2", False)]


def test_parse_packages() -> None:
    html = dependents_page([], menu=MENU)
    assert parse_packages(html) == [
        PackageOption("pkg/default", "ID0", True),
        PackageOption("pkg/other", "ID1", False),
        PackageOption("pkg/third", "ID2", False),
    ]
    assert parse_packages(DEPENDENTS_HTML_LAST_PAGE) == []


@pytest.mark.parametrize(
    ("count", "expected_tail"), [(0, "none"), (3, "n2"), (12, "n9, … and 2 more")]
)
def test_format_names(count: int, expected_tail: str) -> None:
    assert format_names([f"n{i}" for i in range(count)]).endswith(expected_tail)


async def test_scrape_records_packages(mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
    mock_http.get(FIRST_URL, body=dependents_page([("delta", "app", 80)], menu=MENU))
    result = await scrape_dependents(
        session, "https://github.com/owner/repo", rows=10, rate_limiter=fast_limiter()
    )
    assert result.package == "pkg/default"
    assert result.packages == ["pkg/default", "pkg/other", "pkg/third"]


OTHER_URL = (
    "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY&package_id=ID1"
)


async def test_package_name_switches_package(
    mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    mock_http.get(FIRST_URL, body=dependents_page([("skip", "me", 999)], menu=MENU))
    switched_menu = [("pkg/default", "ID0", False), ("pkg/other", "ID1", True)]
    mock_http.get(OTHER_URL, body=dependents_page([("delta", "app", 80)], menu=switched_menu))
    result = await scrape_dependents(
        session,
        "https://github.com/owner/repo",
        rows=10,
        max_pages=1,
        package_name="pkg/other",
        rate_limiter=fast_limiter(),
    )
    assert [r.owner for r in result.repos] == ["delta"]  # lookup page rows not collected
    assert result.pages_scraped == 1  # lookup page not counted
    assert result.package == "pkg/other"
    assert len(mock_http.requests[("GET", OTHER_URL)]) == 1


async def test_package_name_already_selected_makes_no_extra_request(
    mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    mock_http.get(FIRST_URL, body=dependents_page([("delta", "app", 80)], menu=MENU))
    result = await scrape_dependents(
        session,
        "https://github.com/owner/repo",
        rows=10,
        package_name="pkg/default",
        rate_limiter=fast_limiter(),
    )
    assert mock_http.request_count == 1
    assert result.package == "pkg/default"


async def test_package_name_with_package_dependents(
    mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    base = "https://github.com/owner/repo/network/dependents?dependent_type=PACKAGE"
    mock_http.get(base, body=dependents_page([], menu=MENU))
    mock_http.get(base + "&package_id=ID1", body=DEPENDENTS_HTML_LAST_PAGE)
    await scrape_dependents(
        session,
        "https://github.com/owner/repo",
        rows=10,
        dependent_type=DependentType.PACKAGE,
        package_name="pkg/other",
        rate_limiter=fast_limiter(),
    )
    assert len(mock_http.requests[("GET", base + "&package_id=ID1")]) == 1


@pytest.mark.parametrize(
    ("menu", "message"),
    [
        ([], r"^no package named 'nope'; available: none$"),
        (MENU, r"^no package named 'nope'; available: pkg/default, pkg/other, pkg/third$"),
        (
            [(f"p{i:03}", f"ID{i}", i == 0) for i in range(100)],
            r"available: p000, .*, … and 90 more \(GitHub lists only the first 100 packages",
        ),
    ],
    ids=["no-dropdown", "three", "capped"],
)
async def test_unknown_package_name_raises(
    menu: list[tuple[str, str, bool]],
    message: str,
    mock_http: FakeHTTP,
    session: httpx2.AsyncClient,
) -> None:
    mock_http.get(FIRST_URL, body=dependents_page([], menu=menu))
    with pytest.raises(NotFoundError, match=message):
        await scrape_dependents(
            session,
            "https://github.com/owner/repo",
            rows=10,
            package_name="nope",
            rate_limiter=fast_limiter(),
        )
