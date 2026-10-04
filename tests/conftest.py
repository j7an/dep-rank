"""Shared test fixtures for dep-rank."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest

# aiohttp 3.14 added a required keyword-only ``stream_writer`` argument to
# ``ClientResponse.__init__``. aioresponses (<=0.7.8) builds mocked responses
# without it, so every mocked request raises ``TypeError: ... missing 1
# required keyword-only argument: 'stream_writer'``. aiohttp only reads
# ``stream_writer.output_size``, so a ``Mock(output_size=0)`` suffices.
#
# This mirrors the upstream fix (aioresponses#288, tracking aioresponses#289).
# The signature guard makes it a no-op on aiohttp < 3.14 and once aioresponses
# ships a release that supplies the argument itself; remove this shim then.
_response_init = aiohttp.ClientResponse.__init__
if "stream_writer" in inspect.signature(_response_init).parameters:

    def _patched_response_init(self: aiohttp.ClientResponse, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stream_writer", Mock(output_size=0))
        _response_init(self, *args, **kwargs)

    # aiohttp's constructor is an overloaded method; this test-only compatibility shim
    # deliberately accepts its complete call surface to add the missing keyword.
    aiohttp.ClientResponse.__init__ = _patched_response_init  # type: ignore[method-assign]


def dependents_page(
    items: Sequence[tuple[str, str, int]],
    *,
    next_page: int | None = None,
    repos: int = 90,
    packages: int | None = None,
) -> str:
    """Build a valid dependents page with counts, repository rows, and pagination."""
    package_link = (
        f'<a class="btn-link" href="/owner/repo/network/dependents?dependent_type=PACKAGE">'
        f"{packages} Packages</a>"
        if packages is not None
        else ""
    )
    rows = "".join(
        f'<div class="flex-items-center">'
        f'<span><a class="text-bold" href="/{owner}/{name}">{owner}/{name}</a></span>'
        f"<div><span>{stars:,}</span></div></div>"
        for owner, name, stars in items
    )
    nav = (
        f'<a href="/owner/repo/network/dependents?page={next_page}">Next</a>'
        if next_page is not None
        else '<a href="/owner/repo/network/dependents?page=1">Previous</a>'
    )
    return f"""
    <html><body>
    <div class="table-list-header-toggle states flex-auto pl-0">
        <a class="btn-link selected"
           href="/owner/repo/network/dependents?dependent_type=REPOSITORY">{repos} Repositories</a>
        {package_link}
    </div>
    <div id="dependents"><div class="Box">{rows}</div>
    <div class="paginate-container"><div>{nav}</div></div></div>
    </body></html>
    """


DEPENDENTS_HTML_PAGE_1 = dependents_page(
    [("alpha", "framework", 12500), ("beta", "toolkit", 3200), ("gamma", "utils", 150)],
    next_page=2,
)
DEPENDENTS_HTML_LAST_PAGE = dependents_page([("delta", "app", 80)])
DEPENDENTS_HTML_NO_RESULTS = dependents_page([], repos=0)
DEPENDENTS_HTML_WITH_COUNTS_PAGE_1 = dependents_page(
    [("alpha", "framework", 12500)], next_page=2, repos=900, packages=150
)
DEPENDENTS_HTML_WITH_COUNTS = dependents_page(
    [("alpha", "framework", 12500)], repos=900, packages=150
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DEP_RANK_TOKEN is not leaked between tests."""
    monkeypatch.delenv("DEP_RANK_TOKEN", raising=False)
