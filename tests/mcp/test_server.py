"""Tests for the FastMCP server: tool surface, token gate, call lock, errors, and progress."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.logging import LogMessage
from mcp.server.session import ServerSession
from mcp.types import CallToolResult as MCPCallToolResult
from mcp.types import TextContent

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import (
    CodeSearchResult,
    DependentsResult,
    DependentType,
    RetryStatus,
    ScrapeSnapshot,
)
from dep_rank.mcp import server
from dep_rank.mcp.server import mcp
from tests.conftest import FakeHTTP, dependents_page, make_repo

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


def _snapshot(pages_scraped: int, estimated_total_pages: int) -> ScrapeSnapshot:
    return ScrapeSnapshot(
        top_k=[],
        pages_scraped=pages_scraped,
        estimated_total_pages=estimated_total_pages,
        estimated_total_dependents=None,
        matched_count=0,
    )


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


@dataclass
class _Recorder:
    """Collects the progress and log notifications a ``Client`` receives."""

    progress: list[tuple[float, float | None, str | None]] = field(default_factory=list)
    logs: list[LogMessage] = field(default_factory=list)

    async def on_progress(self, progress: float, total: float | None, message: str | None) -> None:
        self.progress.append((progress, total, message))

    async def on_log(self, message: LogMessage) -> None:
        self.logs.append(message)

    def warnings(self) -> list[str]:
        return [str(m.data) for m in self.logs if m.level == "warning"]


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


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
            timeout=QUICK_TIMEOUT,
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

        assert await asyncio.wait_for(client.ping(), timeout=QUICK_TIMEOUT)
        tools = await asyncio.wait_for(client.list_tools(), timeout=QUICK_TIMEOUT)
        assert not call.done()

        release.set()
        result = await call

    assert {tool.name for tool in tools} == {"get_dependents", "search_dependents"}
    assert not result.is_error, _text(result)


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


async def test_page_progress_reported(
    token: str, core_deps: AsyncMock, recorder: _Recorder
) -> None:
    async def fake(*_args: Any, **kwargs: Any) -> DependentsResult:
        await kwargs["on_page"](_snapshot(pages_scraped=3, estimated_total_pages=40))
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp, progress_handler=recorder.on_progress) as client:
        result = await client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)

    assert not result.is_error, _text(result)
    assert len(recorder.progress) == 1, recorder.progress
    progress, total, message = recorder.progress[0]
    assert (progress, total) == (3, 200)
    assert message is not None
    assert "3 pages" in message
    assert "~40" in message


def _assert_retry_facts(text: str) -> None:
    for fact in ("rate limit", "page 4", "in 30s", "1/5"):
        assert fact in text, (fact, text)


@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
async def test_retry_reported_as_progress_and_warning(
    tool: str,
    token: str,
    core_deps: AsyncMock,
    core_search: AsyncMock,
    recorder: _Recorder,
) -> None:
    async def fake(*_args: Any, **kwargs: Any) -> DependentsResult | CodeSearchResult:
        await kwargs["on_page"](_snapshot(pages_scraped=3, estimated_total_pages=40))
        await kwargs["on_retry"](RetryStatus(page=4, attempt=1, max_retries=5, delay=30.0))
        return _dependents_result() if tool == "get_dependents" else _search_result()

    core_deps.side_effect = fake
    core_search.side_effect = fake
    async with Client(
        mcp, progress_handler=recorder.on_progress, log_handler=recorder.on_log
    ) as client:
        result = await client.call_tool(tool, TOOL_ARGS[tool], raise_on_error=False)

    assert not result.is_error, _text(result)
    assert len(recorder.progress) == 2, recorder.progress
    progress, total, message = recorder.progress[1]
    # Past the last page count, so progress keeps increasing as MCP requires.
    assert progress == pytest.approx(3 + 1 / 6)
    assert total == 200
    assert message is not None
    _assert_retry_facts(message)
    retry_warnings = [text for text in recorder.warnings() if "rate limit" in text]
    assert len(retry_warnings) == 1, recorder.logs
    _assert_retry_facts(retry_warnings[0])


async def test_retry_progress_strictly_increases(
    token: str, core_deps: AsyncMock, recorder: _Recorder
) -> None:
    async def fake(*_args: Any, **kwargs: Any) -> DependentsResult:
        await kwargs["on_page"](_snapshot(pages_scraped=3, estimated_total_pages=40))
        for attempt in (1, 2):
            await kwargs["on_retry"](
                RetryStatus(page=4, attempt=attempt, max_retries=5, delay=30.0)
            )
        await kwargs["on_page"](_snapshot(pages_scraped=4, estimated_total_pages=40))
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp, progress_handler=recorder.on_progress) as client:
        result = await client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)

    assert not result.is_error, _text(result)
    values = [progress for progress, _, _ in recorder.progress]
    assert len(values) == 4, recorder.progress
    page_3, retry_1, retry_2, page_4 = values
    assert page_3 < retry_1 < retry_2 < page_4
    assert (page_3, page_4) == (3, 4)


async def test_retry_delay_rounds_up_to_whole_seconds(
    token: str, core_deps: AsyncMock, recorder: _Recorder
) -> None:
    async def fake(*_args: Any, **kwargs: Any) -> DependentsResult:
        await kwargs["on_retry"](RetryStatus(page=4, attempt=1, max_retries=5, delay=7.2))
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp, log_handler=recorder.on_log) as client:
        result = await client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)

    assert not result.is_error, _text(result)
    retry_warnings = [text for text in recorder.warnings() if "rate limit" in text]
    assert len(retry_warnings) == 1, recorder.logs
    assert "in 8s" in retry_warnings[0], retry_warnings[0]


async def test_retry_warning_without_progress_token(
    token: str,
    core_deps: AsyncMock,
    recorder: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(*_args: Any, **kwargs: Any) -> DependentsResult:
        await kwargs["on_retry"](RetryStatus(page=4, attempt=1, max_retries=5, delay=30.0))
        return _dependents_result()

    core_deps.side_effect = fake
    # Context.report_progress calls this only when the request carried a progress token.
    send_progress = AsyncMock()
    monkeypatch.setattr(ServerSession, "send_progress_notification", send_progress)
    async with Client(mcp, log_handler=recorder.on_log) as client:
        # FastMCP's Client always attaches a progress token; the raw mcp session omits
        # it when there is no progress callback.
        result = await client.session.call_tool(
            "get_dependents", {"url": URL}, progress_callback=None
        )
        send_progress.assert_not_awaited()
        # Control: the spy does see progress once a token is sent, so the check above
        # is not vacuous.
        await client.call_tool("get_dependents", {"url": URL})
        send_progress.assert_awaited()

    assert isinstance(result, MCPCallToolResult)
    assert not result.is_error, result
    retry_warnings = [text for text in recorder.warnings() if "rate limit" in text]
    assert len(retry_warnings) == 2, recorder.logs
    _assert_retry_facts(retry_warnings[0])


async def test_lock_wait_reported(
    token: str,
    core_deps: AsyncMock,
    recorder: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = _SignallingLock()
    monkeypatch.setattr(server, "_lock", lambda: lock)
    a_started = asyncio.Event()
    release_a = asyncio.Event()

    async def fake(*_args: Any, **_kwargs: Any) -> DependentsResult:
        if not a_started.is_set():
            a_started.set()
            # Held until call B has queued; the cap only keeps a broken server from hanging.
            await asyncio.wait_for(release_a.wait(), timeout=2 * QUICK_TIMEOUT)
        return _dependents_result()

    core_deps.side_effect = fake
    async with Client(mcp) as client:
        call_a = asyncio.create_task(
            client.call_tool("get_dependents", {"url": URL}, raise_on_error=False)
        )
        await asyncio.wait_for(a_started.wait(), timeout=QUICK_TIMEOUT)
        call_b = asyncio.create_task(
            client.call_tool(
                "get_dependents",
                {"url": URL},
                progress_handler=recorder.on_progress,
                raise_on_error=False,
            )
        )
        await asyncio.wait_for(lock.contended.wait(), timeout=QUICK_TIMEOUT)
        release_a.set()
        result_a, result_b = await asyncio.gather(call_a, call_b)

    assert not result_a.is_error, _text(result_a)
    assert not result_b.is_error, _text(result_b)
    assert any(
        message is not None and "waiting for another dep-rank call" in message
        for _, _, message in recorder.progress
    ), recorder.progress


@pytest.mark.parametrize("tool", list(TOOL_ARGS))
async def test_cancelled_call_drains_refresh_before_releasing(
    tool: str, monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP, tmp_path: Path
) -> None:
    """A cancelled call finishes its SWR drain and closes its cache before freeing the lock.

    Uses the real scraper, SWR manager and cache. Page 1 is an expired cache entry, so it
    schedules a background refresh, held open here; page 2's foreground fetch never
    returns. Cancelling the MCP request must not cut the scraper's ``swr.drain()`` short:
    the lock stays held until the refresh finishes, and the refresh's cache write lands
    before the cache closes.
    """
    monkeypatch.setenv("DEP_RANK_TOKEN", "tok")
    first = f"{URL}/network/dependents?dependent_type=REPOSITORY"
    cache_dir = str(tmp_path / "cache")  # where the autouse fixture points open_cache
    seed = SqliteCache(cache_dir)
    await seed.initialize()
    page_1 = dependents_page([("a", "one", 100)], next_page=2)
    await seed.put(first, page_1.encode(), etag='"old"', ttl=-1)  # expired: served stale
    await seed.close()

    release_refresh = asyncio.Event()
    foreground_started = asyncio.Event()
    foreground_cancelled = asyncio.Event()

    async def held_refresh(request: httpx2.Request) -> httpx2.Response:
        await release_refresh.wait()
        return httpx2.Response(304)

    async def stuck_foreground(request: httpx2.Request) -> httpx2.Response:
        foreground_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            foreground_cancelled.set()
            raise
        raise AssertionError("unreachable")  # pragma: no cover

    mock_http.get(first, callback=held_refresh)
    mock_http.get(f"{URL}/network/dependents?page=2", callback=stuck_foreground)

    async with Client(mcp) as client:
        call = asyncio.create_task(client.call_tool(tool, TOOL_ARGS[tool]))
        await asyncio.wait_for(foreground_started.wait(), QUICK_TIMEOUT)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        await asyncio.wait_for(foreground_cancelled.wait(), QUICK_TIMEOUT)

        lock = server._lock()

        async def next_call_acquires() -> None:
            async with lock:
                pass

        waiter = asyncio.create_task(next_call_acquires())
        done, _ = await asyncio.wait({waiter}, timeout=0.2)
        assert not done  # the cancelled call is still draining its refresh
        release_refresh.set()
        await asyncio.wait_for(waiter, QUICK_TIMEOUT)

    check = SqliteCache(cache_dir)
    await check.initialize()
    try:
        refreshed = await check.get(first)
    finally:
        await check.close()
    assert refreshed is not None
    assert refreshed["expired"] is False  # the refresh's write landed before teardown


async def test_http_client_honors_proxy_environment() -> None:
    # Docker MCP Toolkit enforces allowHosts (--block-network) via an L7 proxy reached
    # through http_proxy/https_proxy; ignoring them makes every request fail.
    client = server._http_client()
    try:
        assert client.trust_env is True
    finally:
        await client.aclose()
