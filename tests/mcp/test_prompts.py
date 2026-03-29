"""Tests for MCP prompts."""

from __future__ import annotations

import pytest
from fastmcp import Client


class TestPrompts:
    @pytest.mark.asyncio
    async def test_analyze_ecosystem_prompt_exists(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts}
            assert "analyze_ecosystem" in prompt_names

    @pytest.mark.asyncio
    async def test_find_usage_patterns_prompt_exists(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts}
            assert "find_usage_patterns" in prompt_names
