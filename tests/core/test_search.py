"""Tests for GitHub code search."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from dep_rank.core.models import CodeSearchResult, ScrapeReason, ScrapeResult
from dep_rank.core.search import search_code, search_dependents
from tests.conftest import FakeHTTP, fast_limiter, make_repo


@pytest.fixture(autouse=True)
def _fresh_search_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own budget instead of the shared module-level 10/min limiter."""
    monkeypatch.setattr("dep_rank.core.search.SEARCH_RATE_LIMITER", fast_limiter())


@pytest.mark.parametrize(
    ("bad_response", "reason"),
    [
        ({"body": b"<html>not json"}, "invalid JSON"),
        ({"exception": httpx2.ReadTimeout("slow")}, "ReadTimeout"),
        ({"status": 403}, "HTTP 403"),
        ({"exception": httpx2.ConnectError("connection failed")}, "ConnectError"),
    ],
    ids=["bad-json", "read-timeout", "non-200", "connect-error"],
)
async def test_search_reports_repo_on_bad_response(
    bad_response: dict[str, Any], reason: str, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    repos = [make_repo("alpha", "framework"), make_repo("beta", "toolkit")]
    mock_http.get(
        "https://api.github.com/search/code?q=test%20repo%3Aalpha%2Fframework", **bad_response
    )
    mock_http.get(
        "https://api.github.com/search/code?q=test%20repo%3Abeta%2Ftoolkit",
        payload={
            "total_count": 1,
            "items": [
                {"html_url": "https://github.com/beta/toolkit/blob/main/app.py", "path": "app.py"}
            ],
        },
    )

    result = await search_code(session, repos, "test", token="fake")

    assert result.searched_repos == 1
    assert result.failed_repos == {"alpha/framework": reason}
    assert result.incomplete_repos == []
    assert [(hit.repo, hit.file_path) for hit in result.hits] == [(repos[1], "app.py")]


@pytest.mark.parametrize(
    ("incomplete", "items", "expected"),
    [
        (
            True,
            [{"html_url": "https://github.com/alpha/framework/blob/main/a.py", "path": "a.py"}],
            ["alpha/framework"],
        ),
        (True, [], ["alpha/framework"]),
        (False, [], []),
    ],
    ids=["timed-out-with-hits", "timed-out-no-hits", "explicit-false"],
)
async def test_search_reports_incomplete_results(
    incomplete: bool,
    items: list[dict[str, str]],
    expected: list[str],
    mock_http: FakeHTTP,
    session: httpx2.AsyncClient,
) -> None:
    repos = [make_repo("alpha", "framework")]
    mock_http.get(
        "https://api.github.com/search/code?q=test%20repo%3Aalpha%2Fframework",
        payload={"total_count": len(items), "incomplete_results": incomplete, "items": items},
    )

    result = await search_code(session, repos, "test", token="fake")

    assert result.incomplete_repos == expected
    assert result.searched_repos == 1
    assert result.failed_repos == {}
    assert [hit.file_path for hit in result.hits] == [item["path"] for item in items]


class TestSearchCode:
    async def test_basic_search(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        repos = [make_repo("alpha", "framework", stars=5000)]
        search_response = {
            "total_count": 2,
            "items": [
                {
                    "html_url": "https://github.com/alpha/framework/blob/main/src/app.py",
                    "path": "src/app.py",
                    "text_matches": [{"fragment": "import pandas"}],
                },
                {
                    "html_url": "https://github.com/alpha/framework/blob/main/tests/test.py",
                    "path": "tests/test.py",
                    "text_matches": [{"fragment": "import pandas"}, {"fragment": "import pandas"}],
                },
            ],
        }
        mock_http.get(
            "https://api.github.com/search/code?q=import%20pandas%20repo%3Aalpha%2Fframework",
            payload=search_response,
        )
        result = await search_code(session, repos, "import pandas", token="fake")
        assert result.searched_repos == 1
        assert result.incomplete_repos == []
        assert len(result.hits) == 2
        assert result.hits[0].file_path == "src/app.py"

    async def test_respects_max_repos(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(5)]
        for i in range(2):
            mock_http.get(
                f"https://api.github.com/search/code?q=test%20repo%3Ao{i}%2Fr{i}",
                payload={"total_count": 0, "items": []},
            )
        result = await search_code(session, repos, "test", token="fake", max_repos=2)
        assert result.searched_repos == 2
        assert result.failed_repos == {}

    async def test_empty_repos_list(self, session: httpx2.AsyncClient) -> None:
        result = await search_code(session, [], "query", token="fake")
        assert result.searched_repos == 0
        assert len(result.hits) == 0


SOURCE_URL = "https://github.com/o/r"


async def test_search_dependents_bounded_non_adaptive_and_copies_completeness(
    session: httpx2.AsyncClient,
) -> None:
    repo = make_repo("a", "b")
    scraped = ScrapeResult(
        repos=[repo],
        pages_scraped=3,
        max_pages=3,
        estimated_total_pages=9,
        estimated_total_dependents=5,
        complete=False,
        reason=ScrapeReason.MAX_PAGES_REACHED,
    )
    searched = CodeSearchResult(source=repo.url, query="q", hits=[], searched_repos=1)
    on_scraped = AsyncMock()
    with (
        patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock) as scrape_mock,
        patch("dep_rank.core.search.search_code", new_callable=AsyncMock) as search_mock,
    ):
        scrape_mock.return_value = scraped
        search_mock.return_value = searched
        result = await search_dependents(
            session, SOURCE_URL, "q", token="t", max_repos=7, on_scraped=on_scraped
        )
    assert scrape_mock.await_args is not None
    assert scrape_mock.await_args.kwargs["adaptive_stop"] is False
    assert scrape_mock.await_args.kwargs["rows"] == 7
    on_scraped.assert_awaited_once_with(scraped)
    search_mock.assert_awaited_once()
    assert (result.source, result.complete, result.reason) == (
        SOURCE_URL,
        False,
        ScrapeReason.MAX_PAGES_REACHED,
    )


async def test_search_dependents_zero_dependents_makes_no_search_request(
    session: httpx2.AsyncClient, mock_http: FakeHTTP
) -> None:
    scraped = ScrapeResult(
        repos=[],
        pages_scraped=1,
        max_pages=200,
        estimated_total_pages=1,
        estimated_total_dependents=0,
    )
    with patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock) as scrape_mock:
        scrape_mock.return_value = scraped
        result = await search_dependents(session, SOURCE_URL, "q", token="t")
    assert mock_http.request_count == 0
    assert (result.source, result.searched_repos, result.hits) == (SOURCE_URL, 0, [])
    assert (result.complete, result.reason) == (True, None)


ALPHA = "https://api.github.com/search/code?q=test%20repo%3Aalpha%2Fframework"
BETA = "https://api.github.com/search/code?q=test%20repo%3Abeta%2Ftoolkit"
HIT = {"total_count": 1, "items": [{"html_url": "https://github.com/x/blob/a.py", "path": "a.py"}]}


@pytest.mark.parametrize(
    ("status", "headers", "expected_wait"),
    [
        (429, {"retry-after": "5"}, 5),
        (403, {"retry-after": "5"}, 5),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}, 30),
        (429, {}, 60),
        (429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 60),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "soon"}, 60),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "990"}, 1),
    ],
    ids=[
        "429-retry-after",
        "403-retry-after",
        "403-reset",
        "429-no-headers",
        "http-date",
        "bad-reset",
        "reset-passed",
    ],
)
async def test_rate_limited_search_retries_then_succeeds(
    status: int,
    headers: dict[str, str],
    expected_wait: float,
    monkeypatch: pytest.MonkeyPatch,
    mock_http: FakeHTTP,
    session: httpx2.AsyncClient,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.search.asyncio.sleep", sleep)
    monkeypatch.setattr("dep_rank.core.search.time.time", lambda: 1000.0)
    mock_http.get(ALPHA, status=status, headers=headers)
    mock_http.get(ALPHA, payload=HIT)
    result = await search_code(session, [make_repo("alpha", "framework")], "test", token="t")
    assert result.failed_repos == {}
    assert result.searched_repos == 1
    assert len(result.hits) == 1
    assert sleep.await_count == 1
    assert sleep.await_args is not None
    assert sleep.await_args.args[0] == expected_wait  # exactly what GitHub asked


async def test_repeated_limits_back_off_exponentially(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.search.asyncio.sleep", sleep)
    mock_http.get(ALPHA, status=429)
    mock_http.get(ALPHA, status=429)
    mock_http.get(ALPHA, payload=HIT)
    result = await search_code(session, [make_repo("alpha", "framework")], "test", token="t")
    assert result.failed_repos == {}
    assert [c.args[0] for c in sleep.await_args_list] == [60, 120]


async def test_exhausted_retries_fail_fast(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.search.asyncio.sleep", sleep)
    for _ in range(3):
        mock_http.get(ALPHA, status=429, headers={"retry-after": "1"})
    repos = [make_repo("alpha", "framework"), make_repo("beta", "toolkit")]
    result = await search_code(session, repos, "test", token="t")
    assert result.failed_repos == {
        "alpha/framework": "rate limited",
        "beta/toolkit": "rate limited",
    }
    assert result.searched_repos == 0
    assert sleep.await_count == 2
    assert len(mock_http.requests[("GET", ALPHA)]) == 3
    assert mock_http.requests[("GET", BETA)] == []


async def test_wait_above_cap_gives_up_without_sleeping(
    monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("dep_rank.core.search.asyncio.sleep", sleep)
    mock_http.get(ALPHA, status=429, headers={"retry-after": "3600"})
    repos = [make_repo("alpha", "framework"), make_repo("beta", "toolkit")]
    result = await search_code(session, repos, "test", token="t")
    assert result.failed_repos == {
        "alpha/framework": "rate limited",
        "beta/toolkit": "rate limited",
    }
    sleep.assert_not_awaited()
    assert mock_http.requests[("GET", BETA)] == []


async def test_search_dependents_carries_scraped_packages(session: httpx2.AsyncClient) -> None:
    scraped = ScrapeResult(
        repos=[make_repo("a", "b")],
        pages_scraped=1,
        max_pages=1,
        estimated_total_pages=1,
        estimated_total_dependents=1,
        package="p",
        packages=["p", "q"],
    )
    searched = CodeSearchResult(source="x", query="q", hits=[], searched_repos=1)
    with (
        patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock) as scrape_mock,
        patch("dep_rank.core.search.search_code", new_callable=AsyncMock) as search_mock,
    ):
        scrape_mock.return_value = scraped
        search_mock.return_value = searched
        result = await search_dependents(session, SOURCE_URL, "q", token="t", package_name="p")
    assert (result.package, result.packages) == ("p", ["p", "q"])
    assert scrape_mock.await_args is not None
    assert scrape_mock.await_args.kwargs["package_name"] == "p"
