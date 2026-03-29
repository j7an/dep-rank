"""Tests for MCP tools via FastMCP in-memory client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

from dep_rank.core.models import Repository


def _make_repo(owner: str = "alpha", name: str = "framework", stars: int = 5000) -> Repository:
    return Repository(owner=owner, name=name, url=f"https://github.com/{owner}/{name}", stars=stars)


class TestGetTopDependents:
    @pytest.mark.asyncio
    async def test_tool_exists(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "get_top_dependents" in tool_names

    @pytest.mark.asyncio
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_get_top_dependents_call(self, mock_scrape: AsyncMock) -> None:
        mock_scrape.return_value = [_make_repo()]

        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_top_dependents",
                {"url": "https://github.com/django/django", "rows": 5, "min_stars": 1},
            )
            # CallToolResult — check it has content
            assert result is not None
            content = str(result)
            assert "alpha" in content


class _MockContext:
    """Minimal mock of fastmcp.Context for direct tool function testing."""

    def __init__(self, state: dict[str, object]) -> None:
        self.lifespan_context = state
        self._state: dict[str, object] = {}

    async def info(self, msg: str) -> None:
        pass

    async def report_progress(self, progress: int, total: int) -> None:
        pass

    async def set_state(self, key: str, value: object) -> None:
        self._state[key] = value

    async def get_state(self, key: str) -> object:
        return self._state.get(key)


class TestGetDependentDetails:
    @pytest.mark.asyncio
    @patch("dep_rank.core.graphql.enrich_with_graphql", new_callable=AsyncMock)
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_get_dependent_details_direct(
        self, mock_scrape: AsyncMock, mock_enrich: AsyncMock
    ) -> None:
        from dep_rank.mcp.server import get_dependent_details

        repos = [_make_repo()]
        mock_scrape.return_value = repos
        enriched = [repos[0].model_copy(update={"stars": 6000, "description": "A framework"})]
        mock_enrich.return_value = enriched

        ctx = _MockContext({"token": "test-token", "session": AsyncMock(), "cache": AsyncMock()})
        result = await get_dependent_details(
            url="https://github.com/django/django",
            rows=5,
            ctx=ctx,
        )
        assert result is not None
        assert result.repos[0].stars == 6000

    @pytest.mark.asyncio
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_get_dependent_details_no_token_raises(self, mock_scrape: AsyncMock) -> None:
        from dep_rank.mcp.server import get_dependent_details

        ctx = _MockContext({"token": None, "session": AsyncMock(), "cache": AsyncMock()})
        with pytest.raises(Exception, match="DEP_RANK_TOKEN"):
            await get_dependent_details(
                url="https://github.com/django/django",
                rows=5,
                ctx=ctx,
            )

    @pytest.mark.asyncio
    @patch("dep_rank.core.graphql.enrich_with_graphql", new_callable=AsyncMock)
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_get_dependent_details_with_cached_state(
        self, mock_scrape: AsyncMock, mock_enrich: AsyncMock
    ) -> None:
        """When ctx has cached state, scrape is not called."""
        from dep_rank.mcp.server import get_dependent_details

        repos = [_make_repo()]
        enriched = [repos[0].model_copy(update={"stars": 6000})]
        mock_enrich.return_value = enriched

        ctx = _MockContext({"token": "test-token", "session": AsyncMock(), "cache": AsyncMock()})
        # Pre-populate state
        await ctx.set_state(
            "deps:https://github.com/django/django", [r.model_dump() for r in repos]
        )

        result = await get_dependent_details(
            url="https://github.com/django/django",
            rows=5,
            ctx=ctx,
        )
        assert result is not None
        mock_scrape.assert_not_called()


class TestSearchDependentCode:
    @pytest.mark.asyncio
    @patch("dep_rank.mcp.server.search_code", new_callable=AsyncMock)
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_search_dependent_code_direct(
        self, mock_scrape: AsyncMock, mock_search: AsyncMock
    ) -> None:
        from dep_rank.core.models import CodeSearchResult
        from dep_rank.mcp.server import search_dependent_code

        repos = [_make_repo()]
        mock_scrape.return_value = repos
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=1,
        )

        ctx = _MockContext({"token": "test-token", "session": AsyncMock(), "cache": AsyncMock()})
        result = await search_dependent_code(
            url="https://github.com/django/django",
            query="import os",
            max_repos=5,
            ctx=ctx,
        )
        assert result is not None
        assert result.searched_repos == 1

    @pytest.mark.asyncio
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_search_dependent_code_no_token_raises(self, mock_scrape: AsyncMock) -> None:
        from dep_rank.mcp.server import search_dependent_code

        ctx = _MockContext({"token": None, "session": AsyncMock(), "cache": AsyncMock()})
        with pytest.raises(Exception, match="DEP_RANK_TOKEN"):
            await search_dependent_code(
                url="https://github.com/django/django",
                query="import os",
                ctx=ctx,
            )

    @pytest.mark.asyncio
    @patch("dep_rank.mcp.server.search_code", new_callable=AsyncMock)
    @patch("dep_rank.mcp.server.scrape_dependents", new_callable=AsyncMock)
    async def test_search_with_cached_state(
        self, mock_scrape: AsyncMock, mock_search: AsyncMock
    ) -> None:
        """When ctx has cached state, scrape is not called."""
        from dep_rank.core.models import CodeSearchResult
        from dep_rank.mcp.server import search_dependent_code

        repos = [_make_repo()]
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=1,
        )

        ctx = _MockContext({"token": "test-token", "session": AsyncMock(), "cache": AsyncMock()})
        await ctx.set_state(
            "deps:https://github.com/django/django", [r.model_dump() for r in repos]
        )

        result = await search_dependent_code(
            url="https://github.com/django/django",
            query="import os",
            ctx=ctx,
        )
        assert result is not None
        mock_scrape.assert_not_called()


class TestMcpPromptBodies:
    @pytest.mark.asyncio
    async def test_analyze_ecosystem_returns_content(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            result = await client.get_prompt(
                "analyze_ecosystem", arguments={"repo_url": "https://github.com/django/django"}
            )
            assert len(result.messages) > 0

    @pytest.mark.asyncio
    async def test_find_usage_patterns_returns_content(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            result = await client.get_prompt(
                "find_usage_patterns",
                arguments={"repo_url": "https://github.com/django/django", "pattern": "Model"},
            )
            assert len(result.messages) > 0


class TestToolAnnotations:
    @pytest.mark.asyncio
    async def test_tools_have_annotations(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            for tool in tools:
                assert tool.annotations is not None
