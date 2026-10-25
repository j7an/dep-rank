"""Tests for the FastMCP server: tool surface, token gate, call lock, and errors."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.logging import LogMessage
from mcp.types import TextContent

from dep_rank.core.models import CodeSearchResult, DependentsResult, DependentType
from dep_rank.mcp import server
from dep_rank.mcp.server import mcp
from tests.conftest import make_repo

URL = "https://github.com/owner/repo"
TOOL_ARGS: dict[str, dict[str, Any]] = {
    "get_dependents": {"url": URL},
    "search_dependents": {"url": URL, "query": "needle"},
}
# Generous bound for calls that should finish in milliseconds; far below any blocking sleep.
QUICK_TIMEOUT = 5.0


def _dependents_result(**fields: Any) -> DependentsResult:
    values: dict[str, Any] = {
        "source": URL,
        "total_count": 1,
        "filtered_count": 1,
        "repos": [make_repo("alpha", "framework", stars=42)],
        "dependent_type": DependentType.REPOSITORY,
        "scraped_at": datetime(2026, 10, 24, tzinfo=UTC),
    }
    values.update(fields)
    return DependentsResult(**values)


def _search_result() -> CodeSearchResult:
    return CodeSearchResult(source=URL, query="needle", hits=[], searched_repos=0)


def _text(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DEP_RANK_TOKEN", "tok")
    return "tok"


@pytest.fixture
def core_deps(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=_dependents_result())
    monkeypatch.setattr("dep_rank.mcp.server.get_dependents", mock)
    return mock


@pytest.fixture
def core_search(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=_search_result())
    monkeypatch.setattr("dep_rank.mcp.server.search_dependents", mock)
    return mock


class _SignallingLock(asyncio.Lock):
    """An ``asyncio.Lock`` that flags when a caller starts waiting on it while it is held."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        if self.locked():
            self.contended.set()
        return await super().acquire()


async def test_lists_exactly_two_read_only_tools() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert set(tools) == {"get_dependents", "search_dependents"}
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.output_schema is not None
        props = tool.input_schema["properties"]
        assert props["max_pages"]["maximum"] == 1000
        assert props["max_pages"]["minimum"] == 1
        assert props["min_stars"]["minimum"] == 0

    deps_props = tools["get_dependents"].input_schema["properties"]
    search_props = tools["search_dependents"].input_schema["properties"]
    assert deps_props["rows"]["minimum"] == 0
    assert "downloads" in deps_props
    assert search_props["max_repos"]["minimum"] == 1
    assert search_props["query"]["minLength"] == 1


async def test_get_dependents_round_trip(token: str, core_deps: AsyncMock) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_dependents",
            {"url": URL, "downloads": True, "rank_by": "trust"},
            raise_on_error=False,
        )

    assert not result.is_error, _text(result)
    assert result.structured_content == core_deps.return_value.model_dump(mode="json")
    core_deps.assert_awaited_once()
    assert core_deps.await_args is not None
    kwargs = core_deps.await_args.kwargs
    assert kwargs["downloads"] is True
    assert kwargs["rank_by"] == "trust"
    assert kwargs["token"] == token


async def test_search_dependents_round_trip(token: str, core_search: AsyncMock) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_dependents",
            {"url": URL, "query": "needle", "max_repos": 3, "min_stars": 7, "max_pages": 9},
            raise_on_error=False,
        )

    assert not result.is_error, _text(result)
    assert result.structured_content == core_search.return_value.model_dump(mode="json")
    core_search.assert_awaited_once()
    assert core_search.await_args is not None
    assert core_search.await_args.args[1:] == (URL, "needle")
    kwargs = core_search.await_args.kwargs
    assert (kwargs["max_repos"], kwargs["min_stars"], kwargs["max_pages"]) == (3, 7, 9)
    assert kwargs["token"] == token


async def test_non_github_url_is_tool_error(token: str, core_deps: AsyncMock) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_dependents", {"url": "https://gitlab.com/a/b"}, raise_on_error=False
        )

    assert result.is_error
    assert "URL must be a github.com repository URL, got: gitlab.com" in _text(result)
    core_deps.assert_not_awaited()


@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
async def test_core_value_error_is_tool_error(
    tool: str, token: str, core_deps: AsyncMock, core_search: AsyncMock
) -> None:
    core_deps.side_effect = ValueError("boom")
    core_search.side_effect = ValueError("boom")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, TOOL_ARGS[tool], raise_on_error=False)

    assert result.is_error
    assert "boom" in _text(result)


@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
@pytest.mark.parametrize("token_value", [None, "", "  "], ids=["unset", "empty", "blank"])
async def test_missing_token_is_tool_error(
    tool: str,
    token_value: str | None,
    monkeypatch: pytest.MonkeyPatch,
    core_deps: AsyncMock,
    core_search: AsyncMock,
) -> None:
    if token_value is not None:
        monkeypatch.setenv("DEP_RANK_TOKEN", token_value)
    async with Client(mcp) as client:
        result = await client.call_tool(tool, TOOL_ARGS[tool], raise_on_error=False)

    assert result.is_error
    assert "DEP_RANK_TOKEN" in _text(result)
    core_deps.assert_not_awaited()
    core_search.assert_not_awaited()


async def test_calls_serialize(token: str, core_deps: AsyncMock) -> None:
    in_flight = 0
    peak = 0

    async def fake(*_args: Any, **_kwargs: Any) -> DependentsResult:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp) as client:
        results = await asyncio.gather(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False),
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False),
        )

    assert not any(result.is_error for result in results)
    assert core_deps.await_count == 2
    assert peak == 1


async def test_failed_call_releases_lock(token: str, core_deps: AsyncMock) -> None:
    core_deps.side_effect = [ValueError("first fails"), _dependents_result()]
    async with Client(mcp) as client:
        first = await client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)
        second = await asyncio.wait_for(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False),
            timeout=QUICK_TIMEOUT,
        )

    assert first.is_error
    assert not second.is_error, _text(second)


async def test_cancelled_call_releases_lock(
    token: str, core_deps: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _SignallingLock()
    monkeypatch.setattr(server, "_lock", lambda: lock)
    a_started = asyncio.Event()

    async def fake(*_args: Any, **_kwargs: Any) -> DependentsResult:
        if not a_started.is_set():
            a_started.set()
            await asyncio.sleep(10)
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp) as client:
        call_a = asyncio.create_task(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)
        )
        await asyncio.wait_for(a_started.wait(), timeout=QUICK_TIMEOUT)
        call_b = asyncio.create_task(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)
        )
        await asyncio.wait_for(lock.contended.wait(), timeout=QUICK_TIMEOUT)

        call_b.cancel()
        call_a.cancel()
        await asyncio.gather(call_a, call_b, return_exceptions=True)

        call_c = await asyncio.wait_for(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False),
            timeout=1.0,
        )

    assert not call_c.is_error, _text(call_c)
    assert not lock.locked()


async def test_server_answers_during_long_call(token: str, core_deps: AsyncMock) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake(*_args: Any, **_kwargs: Any) -> DependentsResult:
        started.set()
        # Held open until the test releases it, so the probes below run while the
        # call is provably in flight; the cap only keeps a broken server from hanging.
        await asyncio.wait_for(release.wait(), timeout=2 * QUICK_TIMEOUT)
        return _dependents_result()

    core_deps.side_effect = fake
    # `ping` exists only in the handshake-era protocol, so connect in legacy mode.
    async with Client(mcp, mode="legacy") as client:
        call = asyncio.create_task(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)
        )
        await asyncio.wait_for(started.wait(), timeout=QUICK_TIMEOUT)

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        assert await asyncio.wait_for(client.ping(), timeout=QUICK_TIMEOUT)
        ping_elapsed = loop.time() - t0
        t0 = loop.time()
        tools = await asyncio.wait_for(client.list_tools(), timeout=QUICK_TIMEOUT)
        list_elapsed = loop.time() - t0
        assert not call.done()

        release.set()
        result = await call

    assert ping_elapsed < 0.2
    assert list_elapsed < 0.2
    assert {tool.name for tool in tools} == {"get_dependents", "search_dependents"}
    assert not result.is_error, _text(result)


# ctx.warning is delivered, but mcp flags the logging capability deprecated (SEP-2577).
@pytest.mark.filterwarnings("ignore::mcp.shared.exceptions.MCPDeprecationWarning")
async def test_partial_trust_metadata_warns(token: str, core_deps: AsyncMock) -> None:
    core_deps.return_value = _dependents_result(ranked_by="trust", trust_metadata_complete=False)
    messages: list[LogMessage] = []

    async def log_handler(message: LogMessage) -> None:
        messages.append(message)

    async with Client(mcp, log_handler=log_handler) as client:
        result = await client.call_tool(
            "get_dependents", {"url": URL, "rank_by": "trust"}, raise_on_error=False
        )

    assert not result.is_error, _text(result)
    warnings = [str(m.data) for m in messages if m.level == "warning"]
    assert any("partial" in text for text in warnings), messages
