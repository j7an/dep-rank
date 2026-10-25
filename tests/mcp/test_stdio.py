"""Stdio smoke test: the `dep-rank mcp` subprocess speaks only JSON-RPC on stdout."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def test_stdio_lists_tools(tmp_path: Path) -> None:
    # The subprocess does not inherit the in-process cache patch, so isolate the cache on Linux
    # and macOS (Windows resolves it via SHGetFolderPathW and ignores these); list_tools()
    # never opens the cache anyway.
    env = {
        **os.environ,
        "DEP_RANK_TOKEN": "x",
        "XDG_CACHE_HOME": str(tmp_path),
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path),
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", "from dep_rank.cli.app import cli; cli()", "mcp"],
        env=env,
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == {"get_dependents", "search_dependents"}
