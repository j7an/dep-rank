"""Tests for GitHub GraphQL batch enrichment."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.graphql import build_batch_query, enrich_with_graphql
from dep_rank.core.models import Repository


def make_repo(owner: str, name: str, stars: int = 100) -> Repository:
    return Repository(owner=owner, name=name, url=f"https://github.com/{owner}/{name}", stars=stars)


class TestBuildBatchQuery:
    def test_single_repo(self) -> None:
        query = build_batch_query([make_repo("django", "django")])
        assert "repository(owner:" in query
        assert "stargazerCount" in query
        assert "description" in query

    def test_multiple_repos(self) -> None:
        repos = [make_repo("a", "b"), make_repo("c", "d")]
        query = build_batch_query(repos)
        assert "repo_0:" in query
        assert "repo_1:" in query

    def test_batch_limit_100(self) -> None:
        repos = [make_repo(f"owner{i}", f"repo{i}") for i in range(150)]
        query = build_batch_query(repos[:100])
        assert "repo_99:" in query


class TestEnrichWithGraphql:
    @pytest.mark.asyncio
    async def test_enriches_stars_and_description(self) -> None:
        repos = [make_repo("django", "django", stars=80000)]
        graphql_response = {
            "data": {
                "repo_0": {
                    "stargazerCount": 82400,
                    "description": "The Web framework for perfectionists with deadlines.",
                }
            }
        }
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=graphql_response)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert enriched[0].stars == 82400
        assert enriched[0].description == "The Web framework for perfectionists with deadlines."

    @pytest.mark.asyncio
    async def test_handles_null_description(self) -> None:
        repos = [make_repo("a", "b")]
        graphql_response = {"data": {"repo_0": {"stargazerCount": 50, "description": None}}}
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=graphql_response)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert enriched[0].description is None

    @pytest.mark.asyncio
    async def test_batches_over_100(self) -> None:
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(150)]
        response_1 = {
            "data": {f"repo_{i}": {"stargazerCount": i, "description": None} for i in range(100)}
        }
        response_2 = {
            "data": {
                f"repo_{i}": {"stargazerCount": 100 + i, "description": None} for i in range(50)
            }
        }
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=response_1)
            m.post("https://api.github.com/graphql", payload=response_2)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert len(enriched) == 150

    @pytest.mark.asyncio
    async def test_resorts_by_accurate_stars(self) -> None:
        repos = [make_repo("a", "b", stars=200), make_repo("c", "d", stars=100)]
        graphql_response = {
            "data": {
                "repo_0": {"stargazerCount": 50, "description": None},
                "repo_1": {"stargazerCount": 300, "description": None},
            }
        }
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=graphql_response)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert enriched[0].owner == "c"
        assert enriched[1].owner == "a"

    @pytest.mark.asyncio
    async def test_empty_repos_returns_empty(self) -> None:
        """enrich_with_graphql returns [] when passed empty list."""
        async with ClientSession() as session:
            enriched = await enrich_with_graphql(session, [], token="fake-token")
        assert enriched == []

    @pytest.mark.asyncio
    async def test_error_response_falls_back(self) -> None:
        """When GraphQL returns errors instead of data, repos pass through unchanged."""
        repos = [make_repo("a", "b", stars=100)]
        graphql_response = {"errors": [{"message": "rate limited"}]}
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=graphql_response)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert len(enriched) == 1
        assert enriched[0].stars == 100  # unchanged

    @pytest.mark.asyncio
    async def test_missing_repo_data_falls_back(self) -> None:
        """When a specific repo is missing from GraphQL data, it passes through unchanged."""
        repos = [make_repo("a", "b", stars=100)]
        graphql_response: dict[str, object] = {"data": {}}  # repo_0 is missing
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=graphql_response)
            async with ClientSession() as session:
                enriched = await enrich_with_graphql(session, repos, token="fake-token")
        assert len(enriched) == 1
        assert enriched[0].stars == 100  # unchanged


class TestBuildTrustQuery:
    def test_includes_engagement_and_recency_fields(self) -> None:
        from dep_rank.core.graphql import build_trust_query

        q = build_trust_query([make_repo("django", "django")], include_description=False)
        assert "stargazerCount" in q
        assert "forkCount" in q
        assert "issues(states: [OPEN, CLOSED])" in q
        assert "pullRequests(states: [OPEN, CLOSED, MERGED])" in q
        assert "pushedAt" in q
        assert "description" not in q

    def test_include_description_adds_field(self) -> None:
        from dep_rank.core.graphql import build_trust_query

        q = build_trust_query([make_repo("a", "b")], include_description=True)
        assert "description" in q


class TestEnrichWithTrustMetadata:
    @pytest.mark.asyncio
    async def test_populates_signals_and_marks_complete(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        repos = [make_repo("django", "django", stars=80000)]
        payload = {
            "data": {
                "repo_0": {
                    "stargazerCount": 82400,
                    "forkCount": 31000,
                    "issues": {"totalCount": 500},
                    "pullRequests": {"totalCount": 300},
                    "pushedAt": "2026-05-01T12:00:00Z",
                }
            }
        }
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=payload)
            async with ClientSession() as session:
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

    @pytest.mark.asyncio
    async def test_401_short_circuits_to_failed(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        repos = [make_repo("a", "b")]
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", status=401)
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="bad", include_description=False
                )
        assert result.failed is True
        assert result.complete is False

    @pytest.mark.asyncio
    async def test_graphql_error_marks_failed_when_only_batch(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        repos = [make_repo("a", "b")]
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload={"errors": [{"message": "boom"}]})
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is True  # the only batch errored -> no usable metadata

    @pytest.mark.asyncio
    async def test_missing_repo_data_is_partial_not_failed(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        repos = [make_repo("a", "b"), make_repo("c", "d")]
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
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=payload)
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is False
        assert result.complete is False
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None

    @pytest.mark.asyncio
    async def test_data_with_errors_is_partial_not_failed(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

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
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=payload)
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is False  # usable data present
        assert result.complete is False  # errors -> incomplete
        assert result.repos[0].trust_signals is not None
        assert result.repos[1].trust_signals is None

    @pytest.mark.asyncio
    async def test_data_all_null_is_failed(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        # A data object whose every repo is null yields no usable trust_signals -> the
        # batch produced nothing; with no other batch succeeding -> failed=True.
        repos = [make_repo("a", "b")]
        payload = {"data": {"repo_0": None}, "errors": [{"message": "Could not resolve"}]}
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=payload)
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is True
        assert result.complete is False
        assert result.repos[0].trust_signals is None

    @pytest.mark.asyncio
    async def test_multi_batch_one_failed_is_partial(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

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
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", payload=good)  # batch 1 succeeds
            m.post("https://api.github.com/graphql", status=500)  # batch 2 fails
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is False  # at least one batch succeeded
        assert result.complete is False  # the other batch failed
        assert len(result.repos) == 150
        assert result.repos[0].trust_signals is not None
        assert result.repos[100].trust_signals is None  # from the failed batch

    @pytest.mark.asyncio
    async def test_multi_batch_all_failed_is_failed(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        repos = [make_repo(f"o{i}", f"r{i}") for i in range(150)]  # 2 batches
        with aioresponses() as m:
            m.post("https://api.github.com/graphql", status=500)  # batch 1 fails
            m.post("https://api.github.com/graphql", status=500)  # batch 2 fails
            async with ClientSession() as session:
                result = await enrich_with_trust_metadata(
                    session, repos, token="fake", include_description=False
                )
        assert result.failed is True  # no usable metadata at all
        assert result.complete is False

    @pytest.mark.asyncio
    async def test_empty_repos_is_clean(self) -> None:
        from dep_rank.core.graphql import enrich_with_trust_metadata

        async with ClientSession() as session:
            result = await enrich_with_trust_metadata(
                session, [], token="fake", include_description=False
            )
        assert result.repos == []
        assert result.failed is False
        assert result.complete is True
