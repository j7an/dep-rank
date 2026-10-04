"""Tests for GitHub GraphQL batch enrichment."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.graphql import build_trust_query, enrich_with_trust_metadata
from tests.conftest import make_repo


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
        self, description: str | None, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo("a", "b", stars=123).model_copy(update={"description": "Old"})]
        payload = {"data": {"repo_0": {"stargazerCount": 456, "description": description}}}
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=True
        )
        request = next(iter(mock_http.requests.values()))[0]
        assert "description" in request.kwargs["json"]["query"]
        assert result.repos[0].description == description
        assert result.repos[0].stars == 456

    async def test_populates_signals_and_marks_complete(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

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
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
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

    async def test_malformed_created_at_degrades_to_missing(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo("django", "django", stars=80000)]
        payload = {"data": {"repo_0": {"stargazerCount": 82400, "createdAt": "not-a-date"}}}
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is False
        assert result.repos[0].trust_signals is not None
        assert result.repos[0].trust_signals.created_at is None

    async def test_malformed_pushed_at_degrades_recency_not_crash(
        self, mock_http: aioresponses, session: ClientSession
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
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is False
        repo = result.repos[0]
        assert repo.trust_signals is not None
        assert repo.trust_signals.pushed_at is None  # unparseable -> missing recency
        assert repo.trust_signals.forks == 31000  # other signals intact

    async def test_401_short_circuits_to_failed(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        mock_http.post("https://api.github.com/graphql", status=401)
        result = await enrich_with_trust_metadata(
            session, repos, token="bad", include_description=False
        )
        assert result.failed is True
        assert result.complete is False
        assert result.repos == expected

    async def test_graphql_error_marks_failed_when_only_batch(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        mock_http.post("https://api.github.com/graphql", payload={"errors": [{"message": "boom"}]})
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is True  # the only batch errored -> no usable metadata
        assert result.complete is False
        assert result.repos == expected

    async def test_missing_repo_data_is_partial_not_failed(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

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
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is False
        assert result.complete is False
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None
        assert result.repos[0].stars == 10
        assert result.repos[1] == expected[1]

    async def test_data_with_errors_is_partial_not_failed(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

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
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is False  # usable data present
        assert result.complete is False  # errors -> incomplete
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None

    @pytest.mark.parametrize("data", [None, {"repo_0": None}], ids=["data-null", "repos-null"])
    async def test_data_all_null_is_failed(
        self, data: dict[str, None] | None, mock_http: aioresponses, session: ClientSession
    ) -> None:

        # Null data and all-null repository data both preserve the scraped fallback.
        repos = [make_repo("a", "b")]
        expected = [repo.model_copy(deep=True) for repo in repos]
        payload = {"data": data, "errors": [{"message": "Could not resolve"}]}
        mock_http.post("https://api.github.com/graphql", payload=payload)
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is True
        assert result.complete is False
        assert result.repos[0].trust_signals is None
        assert result.repos == expected

    async def test_multi_batch_one_failed_is_partial(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo(f"o{i}", f"r{i}") for i in range(150)]  # 2 batches (100 + 50)
        good = {
            "data": {
                f"repo_{i}": {
                    "stargazerCount": i,
                    "forkCount": 0,
                    "issues": {"totalCount": 0},
                    "pullRequests": {"totalCount": 0},
                    "pushedAt": None,
                }
                for i in range(100)
            }
        }
        mock_http.post("https://api.github.com/graphql", payload=good)  # batch 1 succeeds
        mock_http.post("https://api.github.com/graphql", status=500)  # batch 2 fails
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is False  # at least one batch succeeded
        assert result.complete is False  # the other batch failed
        assert len(result.repos) == 150
        assert result.repos[0].trust_signals is not None
        assert result.repos[100].trust_signals is None  # from the failed batch

    async def test_multi_batch_all_failed_is_failed(
        self, mock_http: aioresponses, session: ClientSession
    ) -> None:

        repos = [make_repo(f"o{i}", f"r{i}") for i in range(150)]  # 2 batches
        mock_http.post("https://api.github.com/graphql", status=500)  # batch 1 fails
        mock_http.post("https://api.github.com/graphql", status=500)  # batch 2 fails
        result = await enrich_with_trust_metadata(
            session, repos, token="fake", include_description=False
        )
        assert result.failed is True  # no usable metadata at all
        assert result.complete is False

    async def test_empty_repos_is_clean(self, session: ClientSession) -> None:

        result = await enrich_with_trust_metadata(
            session, [], token="fake", include_description=False
        )
        assert result.repos == []
        assert result.failed is False
        assert result.complete is True
