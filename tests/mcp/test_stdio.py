"""Stdio smoke test: the `dep-rank mcp` subprocess speaks only JSON-RPC on stdout."""

from __future__ import annotations

import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def test_stdio_lists_tools() -> None:
    env = {**os.environ, "DEP_RANK_TOKEN": "x"}
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", "from dep_rank.cli.app import cli; cli()", "mcp"],
        env=env,
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == {"get_dependents", "search_dependents"}
