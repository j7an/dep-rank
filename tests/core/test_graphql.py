"""Tests for GitHub GraphQL batch enrichment."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from dep_rank.core.graphql import (
    BATCH_SIZE,
    GRAPHQL_URL,
    build_trust_query,
    enrich_with_trust_metadata,
)
from dep_rank.core.models import Repository, TrustMetadataResult
from tests.conftest import FakeHTTP, StalledBody, make_repo

RunTrust = Callable[..., Awaitable[tuple[TrustMetadataResult, dict[Any, list[Any]]]]]


@pytest.fixture
def run_trust(mock_http: FakeHTTP, session: httpx2.AsyncClient) -> RunTrust:
    """Queue GraphQL responses (dict = JSON payload, int = HTTP status) and run enrichment."""

    async def _run_trust(
        repos: list[Repository],
        *responses: dict[str, Any] | int,
        include_description: bool = False,
        token: str = "fake",  # noqa: S107 (test placeholder, not a credential)
    ) -> tuple[TrustMetadataResult, dict[Any, list[Any]]]:
        for response in responses:
            if isinstance(response, int):
                mock_http.post(GRAPHQL_URL, status=response)
            else:
                mock_http.post(GRAPHQL_URL, payload=response)
        result = await enrich_with_trust_metadata(
            session, repos, token=token, include_description=include_description
        )
        return result, mock_http.requests

    return _run_trust


class TestBuildTrustQuery:
    def test_includes_engagement_and_recency_fields(self) -> None:

        q = build_trust_query([make_repo("django", "django")], include_description=False)
        assert "stargazerCount" in q
        assert "forkCount" in q
        assert "issues(states: [OPEN, CLOSED])" in q
        assert "pullRequests(states: [OPEN, CLOSED, MERGED])" in q
        assert "pushedAt" in q
        assert "isArchived" in q
        assert "isDisabled" in q
        assert "createdAt" in q
        assert "description" not in q

    def test_include_description_adds_field(self) -> None:

        q = build_trust_query([make_repo("a", "b")], include_description=True)
        assert "description" in q

    def test_two_repository_aliases(self) -> None:

        query = build_trust_query(
            [make_repo("a", "b"), make_repo("c", "d")], include_description=True
        )
        assert 'repo_0: repository(owner: "a", name: "b")' in query
        assert 'repo_1: repository(owner: "c", name: "d")' in query


class TestEnrichWithTrustMetadata:
    @pytest.mark.parametrize("description", ["Fresh description", None], ids=["text", "null"])
    async def test_applies_requested_description(
        self, description: str | None, run_trust: RunTrust
    ) -> None:

        repos = [make_repo("a", "b", stars=123).model_copy(update={"description": "Old"})]
        payload = {"data": {"repo_0": {"stargazerCount": 456, "description": description}}}
        result, requests = await run_trust(repos, payload, include_description=True)
        request = next(iter(requests.values()))[0]
        assert "description" in json.loads(request.content)["query"]
        assert result.repos[0].description == description
        assert result.repos[0].stars == 456

    async def test_populates_signals_and_marks_complete(self, run_trust: RunTrust) -> None:

        repos = [make_repo("django", "django", stars=80000)]
        payload = {
            "data": {
                "repo_0": {
                    "stargazerCount": 82400,
                    "forkCount": 31000,
                    "issues": {"totalCount": 500},
                    "pullRequests": {"totalCount": 300},
                    "pushedAt": "2026-05-01T12:00:00Z",
                    "isArchived": True,
                    "isDisabled": False,
                    "createdAt": "2005-07-13T00:00:00Z",
                }
            }
        }
        result, _ = await run_trust(repos, payload)
        assert result.failed is False
        assert result.complete is True
        repo = result.repos[0]
        assert repo.stars == 82400
        assert repo.trust_signals is not None
        assert repo.trust_signals.forks == 31000
        assert repo.trust_signals.issues == 500
        assert repo.trust_signals.pull_requests == 300
        assert repo.trust_signals.pushed_at is not None
        assert repo.trust_signals.is_archived is True
        assert repo.trust_signals.is_disabled is False
        assert repo.trust_signals.created_at == datetime(2005, 7, 13, tzinfo=UTC)

    async def test_malformed_created_at_degrades_to_missing(self, run_trust: RunTrust) -> None:

        repos = [make_repo("django", "django", stars=80000)]
        payload = {"data": {"repo_0": {"stargazerCount": 82400, "createdAt": "not-a-date"}}}
        result, _ = await run_trust(repos, payload)
        assert result.failed is False
        assert result.repos[0].trust_signals is not None
        assert result.repos[0].trust_signals.created_at is None

    async def test_malformed_pushed_at_degrades_recency_not_crash(
        self, run_trust: RunTrust
    ) -> None:
        # A non-ISO pushedAt must not abort the run; recency degrades to "missing"
        # (pushed_at=None) while the rest of the signals are still applied.

        repos = [make_repo("django", "django", stars=80000)]
        payload = {
            "data": {
                "repo_0": {
                    "stargazerCount": 82400,
                    "forkCount": 31000,
                    "issues": {"totalCount": 500},
                    "pullRequests": {"totalCount": 300},
                    "pushedAt": "not-a-date",
                }
            }
        }
        result, _ = await run_trust(repos, payload)
        assert result.failed is False
        repo = result.repos[0]
        assert repo.trust_signals is not None
        assert repo.trust_signals.pushed_at is None  # unparseable -> missing recency
        assert repo.trust_signals.forks == 31000  # other signals intact

    async def test_401_short_circuits_to_failed(self, run_trust: RunTrust) -> None:

        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        result, _ = await run_trust(repos, 401, token="bad")
        assert result.failed is True
        assert result.complete is False
        assert result.repos == expected

    async def test_graphql_error_marks_failed_when_only_batch(self, run_trust: RunTrust) -> None:

        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        result, _ = await run_trust(repos, {"errors": [{"message": "boom"}]})
        assert result.failed is True  # the only batch errored -> no usable metadata
        assert result.complete is False
        assert result.repos == expected

    async def test_missing_repo_data_is_partial_not_failed(self, run_trust: RunTrust) -> None:

        repos = [make_repo("a", "b"), make_repo("c", "d")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        payload = {
            "data": {
                "repo_0": {
                    "stargazerCount": 10,
                    "forkCount": 1,
                    "issues": {"totalCount": 1},
                    "pullRequests": {"totalCount": 1},
                    "pushedAt": "2026-01-01T00:00:00Z",
                }
                # repo_1 missing
            }
        }
        result, _ = await run_trust(repos, payload)
        assert result.failed is False
        assert result.complete is False
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None
        assert result.repos[0].stars == 10
        assert result.repos[1] == expected[1]

    async def test_data_with_errors_is_partial_not_failed(self, run_trust: RunTrust) -> None:

        repos = [make_repo("a", "b"), make_repo("c", "d")]
        payload = {
            "data": {
                "repo_0": {
                    "stargazerCount": 10,
                    "forkCount": 1,
                    "issues": {"totalCount": 1},
                    "pullRequests": {"totalCount": 1},
                    "pushedAt": "2026-01-01T00:00:00Z",
                },
                "repo_1": None,
            },
            "errors": [{"message": "Could not resolve repo_1"}],
        }
        result, _ = await run_trust(repos, payload)
        assert result.failed is False  # usable data present
        assert result.complete is False  # errors -> incomplete
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None

    @pytest.mark.parametrize("data", [None, {"repo_0": None}], ids=["data-null", "repos-null"])
    async def test_data_all_null_is_failed(
        self, data: dict[str, None] | None, run_trust: RunTrust
    ) -> None:

        # Null data and all-null repository data both preserve the scraped fallback.
        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        payload = {"data": data, "errors": [{"message": "Could not resolve"}]}
        result, _ = await run_trust(repos, payload)
        assert result.failed is True
        assert result.complete is False
        assert result.repos[0].trust_signals is None
        assert result.repos == expected

    def test_batch_size_stays_under_observed_resource_limit(self) -> None:
        # Live probe: one 74-repo query hit RESOURCE_LIMITS_EXCEEDED from alias ~41 on;
        # a batch above that loses trust signals for every repo past the cutoff.
        assert BATCH_SIZE <= 40

    async def test_multi_batch_one_failed_is_partial(self, run_trust: RunTrust) -> None:

        total = BATCH_SIZE + BATCH_SIZE // 2  # 2 batches (full + half)
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(total)]
        good = {
            "data": {
                f"repo_{i}": {
                    "stargazerCount": i,
                    "forkCount": 0,
                    "issues": {"totalCount": 0},
                    "pullRequests": {"totalCount": 0},
                    "pushedAt": None,
                }
                for i in range(BATCH_SIZE)
            }
        }
        result, _ = await run_trust(repos, good, 500)
        assert result.failed is False  # at least one batch succeeded
        assert result.complete is False  # the other batch failed
        assert len(result.repos) == total
        assert result.repos[0].trust_signals is not None
        assert result.repos[BATCH_SIZE].trust_signals is None  # from the failed batch

    async def test_multi_batch_all_failed_is_failed(self, run_trust: RunTrust) -> None:

        total = BATCH_SIZE + BATCH_SIZE // 2  # 2 batches
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(total)]
        result, _ = await run_trust(repos, 500, 500)
        assert result.failed is True  # no usable metadata at all
        assert result.complete is False

    async def test_empty_repos_is_clean(self, session: httpx2.AsyncClient) -> None:

        result = await enrich_with_trust_metadata(
            session, [], token="fake", include_description=False
        )
        assert result.repos == []
        assert result.failed is False
        assert result.complete is True


async def test_error_status_with_stalled_body_is_handled(
    mock_http: FakeHTTP, session: httpx2.AsyncClient
) -> None:
    async def stalled_502(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, stream=StalledBody())

    repos = [make_repo("a", "b")]
    mock_http.post(GRAPHQL_URL, callback=stalled_502)
    result = await enrich_with_trust_metadata(session, repos, token="fake")
    assert result.failed is True
    assert result.complete is False
    assert result.repos == repos
    assert result.repos[0].trust_signals is None
