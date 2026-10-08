"""Per-request HTTP configuration across the core request sites."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest

from dep_rank.core.graphql import GRAPHQL_URL, enrich_with_trust_metadata
from dep_rank.core.scraper import REQUEST_TIMEOUT, _get_once
from dep_rank.core.search import search_code
from dep_rank.core.star_history import STAR_HISTORY_URL, WINDOW_WEEKS, check_star_history
from tests.conftest import FakeHTTP, fast_limiter, make_repo


@pytest.mark.parametrize(
    "site", ["_get_once", "enrich_with_trust_metadata", "search_code", "check_star_history"]
)
async def test_request_sites_follow_redirects_and_set_timeout(
    site: str, mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    repo = make_repo("alpha", "framework")
    target = "https://api.github.com/redirected"
    if site == "_get_once":
        url = "https://github.com/owner/repo/network/dependents"
        mock_http.get(url, status=301, headers={"Location": target})
        mock_http.get(target, body=b"dependents page")
        attempt = await _get_once(session, url, fast_limiter(), {}, None, None)
        outcome_succeeded = attempt.body is not None
    elif site == "enrich_with_trust_metadata":
        mock_http.post(GRAPHQL_URL, status=301, headers={"Location": target})
        mock_http.get(target, payload={"data": {"repo_0": {"stargazerCount": 456}}})
        metadata = await enrich_with_trust_metadata(session, [repo], token="fake")
        outcome_succeeded = (
            metadata.repos[0].stars == 456 and metadata.repos[0].trust_signals is not None
        )
    elif site == "search_code":
        url = "https://api.github.com/search/code?q=test%20repo%3Aalpha%2Fframework"
        mock_http.get(url, status=301, headers={"Location": target})
        mock_http.get(
            target,
            payload={
                "total_count": 1,
                "items": [{"html_url": f"{repo.url}/blob/main/app.py", "path": "app.py"}],
            },
        )
        search = await search_code(session, [repo], "test", token="fake")
        outcome_succeeded = len(search.hits) == 1
    else:
        url = STAR_HISTORY_URL.format(owner=repo.owner, name=repo.name)
        mock_http.get(f"{url}?per_page={WINDOW_WEEKS}", status=301, headers={"Location": target})
        mock_http.get(target, payload=[])
        _, history = await check_star_history(
            session, [repo], token="fake", now=datetime(2026, 10, 7, tzinfo=UTC)
        )
        outcome_succeeded = (
            history.repos_checked == 1 and "alpha/framework" not in history.unavailable
        )

    assert outcome_succeeded
    assert all(
        request.extensions["timeout"]["read"] == REQUEST_TIMEOUT
        for requests in mock_http.requests.values()
        for request in requests
    )
