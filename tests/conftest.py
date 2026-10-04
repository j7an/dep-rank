"""Shared test fixtures for dep-rank."""

from __future__ import annotations

import inspect
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

DEPENDENTS_HTML_PAGE_1 = """
<html>
    <body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                90
                Repositories
            </a>
        </div>
        <div id="dependents">
            <div class="Box">
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/alpha/framework">alpha/framework</a>
                    </span>
                    <div>
                        <span>12,500</span>
                    </div>
                </div>
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/beta/toolkit">beta/toolkit</a>
                    </span>
                    <div>
                        <span>3,200</span>
                    </div>
                </div>
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/gamma/utils">gamma/utils</a>
                    </span>
                    <div>
                        <span>150</span>
                    </div>
                </div>
            </div>
            <div class="paginate-container">
                <div>
                    <a href="/owner/repo/network/dependents?page=2">Next</a>
                </div>
            </div>
        </div>
    </body>
</html>
"""

DEPENDENTS_HTML_LAST_PAGE = """
<html>
    <body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                90
                Repositories
            </a>
        </div>
        <div id="dependents">
            <div class="Box">
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/delta/app">delta/app</a>
                    </span>
                    <div>
                        <span>80</span>
                    </div>
                </div>
            </div>
            <div class="paginate-container">
                <div>
                    <a href="/owner/repo/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </div>
    </body>
</html>
"""

DEPENDENTS_HTML_NO_RESULTS = """
<html>
    <body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                0
                Repositories
            </a>
        </div>
        <div id="dependents">
            <div class="Box">
            </div>
        </div>
    </body>
</html>
"""

DEPENDENTS_HTML_WITH_COUNTS_PAGE_1 = """
<html>
    <body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                900
                Repositories
            </a>
            <a class="btn-link " href="/owner/repo/network/dependents?dependent_type=PACKAGE">
                150
                Packages
            </a>
        </div>
        <div id="dependents">
            <div class="Box">
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/alpha/framework">alpha/framework</a>
                    </span>
                    <div>
                        <span>12,500</span>
                    </div>
                </div>
            </div>
            <div class="paginate-container">
                <div>
                    <a href="/owner/repo/network/dependents?page=2">Next</a>
                </div>
            </div>
        </div>
    </body>
</html>
"""

DEPENDENTS_HTML_WITH_COUNTS = """
<html>
    <body>
        <div class="table-list-header-toggle states flex-auto pl-0">
            <a class="btn-link selected"
               href="/owner/repo/network/dependents?dependent_type=REPOSITORY">
                900
                Repositories
            </a>
            <a class="btn-link " href="/owner/repo/network/dependents?dependent_type=PACKAGE">
                150
                Packages
            </a>
        </div>
        <div id="dependents">
            <div class="Box">
                <div class="flex-items-center">
                    <span>
                        <a class="text-bold" href="/alpha/framework">alpha/framework</a>
                    </span>
                    <div>
                        <span>12,500</span>
                    </div>
                </div>
            </div>
            <div class="paginate-container">
                <div>
                    <a href="/owner/repo/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </div>
    </body>
</html>
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DEP_RANK_TOKEN is not leaked between tests."""
    monkeypatch.delenv("DEP_RANK_TOKEN", raising=False)
