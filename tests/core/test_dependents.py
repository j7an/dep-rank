"""Tests for the caller-owned dependents library pipeline."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.dependents import get_dependents
from dep_rank.core.models import ScrapeResult
from tests.conftest import dependents_page

URL = "https://github.com/owner/repo"
FIRST = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"


@pytest.mark.parametrize(
    ("url", "kwargs"),
    [
        pytest.param(URL, {"rank_by": "Trust"}, id="unknown-rank-by"),
        pytest.param(URL, {"descriptions": True}, id="descriptions-no-token"),
        pytest.param(URL, {"trust_check": True, "token": "t"}, id="trust-check-without-trust"),
        pytest.param(URL, {"rank_by": "trust"}, id="trust-no-token"),
        pytest.param(URL, {"rank_by": "trust", "token": ""}, id="trust-empty-token"),
        pytest.param("not-a-url", {}, id="invalid-url"),
    ],
)
async def test_invalid_input_raises_before_any_request(
    session: ClientSession, mock_http: aioresponses, url: str, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        await get_dependents(session, url, **kwargs)
    assert sum(len(v) for v in mock_http.requests.values()) == 0


async def test_defaults_rank_by_stars_and_leave_session_open(
    session: ClientSession, mock_http: aioresponses
) -> None:
    mock_http.get(FIRST, body=dependents_page([("a", "one", 100), ("b", "two", 5000)]))
    scraped: list[ScrapeResult] = []

    async def on_scraped(scrape_result: ScrapeResult) -> None:
        scraped.append(scrape_result)

    result = await get_dependents(session, URL, token="t", on_scraped=on_scraped)
    assert [s.pages_scraped for s in scraped] == [1]
    assert [r.name for r in result.repos] == ["two", "one"]
    assert result.ranked_by == "stars"
    assert result.complete is True
    assert result.stale_pages == 0
    assert result.trust_metadata_complete is True
    assert session.closed is False


def test_import_loads_no_cli_dependencies() -> None:
    code = (
        "import sys, dep_rank.core.dependents; "
        "print('click' in sys.modules or 'rich' in sys.modules)"
    )
    out = subprocess.run(  # noqa: S603 - fixed code in the current interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
