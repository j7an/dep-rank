"""Shared test fixtures for dep-rank."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Generator, Iterator

import pytest

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


@contextlib.contextmanager
def isolated_dep_rank_token() -> Iterator[None]:
    """Remove ``DEP_RANK_TOKEN`` for the duration of the block, then restore it.

    ``pop`` (not ``get``) is used so the variable is actually removed from the
    environment, regardless of whether the developer has it exported in their
    shell. On exit the variable is unconditionally cleared first — a block may
    have set it even when it was absent originally, and that value must not
    outlive the block — then the original value, if any, is restored. See
    issue #70.
    """
    original = os.environ.pop("DEP_RANK_TOKEN", None)
    try:
        yield
    finally:
        os.environ.pop("DEP_RANK_TOKEN", None)
        if original is not None:
            os.environ["DEP_RANK_TOKEN"] = original


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Ensure DEP_RANK_TOKEN is not leaked between tests.

    Thin wrapper around :func:`isolated_dep_rank_token`; the logic lives there
    so it can be exercised directly by ``tests/test_conftest.py``.
    """
    with isolated_dep_rank_token():
        yield
