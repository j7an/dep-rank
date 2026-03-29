"""Tests for MCP server lifecycle and component visibility."""

from __future__ import annotations

import pytest
from fastmcp import Client


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_server_starts(self) -> None:
        from dep_rank.mcp.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) > 0
