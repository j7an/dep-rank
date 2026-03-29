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
