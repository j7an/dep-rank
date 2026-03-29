"""Shared test fixtures for dep-rank."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

DEPENDENTS_HTML_PAGE_1 = """
<html>
    <body>
        <div class="table-list-header-toggle">
            <button class="btn-link selected">90 Repositories</button>
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
        <div class="table-list-header-toggle">
            <button class="btn-link selected">90 Repositories</button>
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
        <div class="table-list-header-toggle">
            <button class="btn-link selected">0 Repositories</button>
        </div>
        <div id="dependents">
            <div class="Box">
            </div>
        </div>
    </body>
</html>
"""


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Ensure DEP_RANK_TOKEN is not leaked between tests."""
    original = os.environ.get("DEP_RANK_TOKEN")
    yield
    if original is None:
        os.environ.pop("DEP_RANK_TOKEN", None)
    else:
        os.environ["DEP_RANK_TOKEN"] = original
