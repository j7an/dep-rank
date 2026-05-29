"""Regression tests for shared fixtures defined in ``tests/conftest.py``.

Guards the ``clean_env`` autouse fixture against regressing to a read-only
snapshot (``os.environ.get``) that leaves ``DEP_RANK_TOKEN`` in the
environment during the test body. See issue #70.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from tests import conftest


@pytest.fixture(scope="module", autouse=True)
def _seed_dep_rank_token() -> Generator[None, None, None]:
    """Seed ``DEP_RANK_TOKEN`` before the function-scoped ``clean_env`` runs.

    This MUST stay module-scoped. ``clean_env`` is a *function*-scoped autouse
    fixture in the root ``conftest.py``; at equal scope, autouse fixtures from a
    higher-level conftest instantiate before module-level ones, so a
    function-scoped seed here would lose the ordering race and ``clean_env``
    would clear the env before this ever set it. A higher (module) scope always
    instantiates first, guaranteeing the token is present when ``clean_env``
    runs. Do not "simplify" this to function scope — it silently disables the
    guard below.
    """
    original = os.environ.get("DEP_RANK_TOKEN")
    os.environ["DEP_RANK_TOKEN"] = "fixture-regression-sentinel"  # noqa: S105 (test sentinel, not a real secret)
    yield
    if original is None:
        os.environ.pop("DEP_RANK_TOKEN", None)
    else:
        os.environ["DEP_RANK_TOKEN"] = original


def test_clean_env_clears_dep_rank_token() -> None:
    """``clean_env`` must remove ``DEP_RANK_TOKEN`` for the duration of a test.

    With the seed fixture above guaranteeing the var is set going in, a correct
    ``clean_env`` (using ``os.environ.pop``) leaves it absent here. A regression
    to ``os.environ.get`` would leave the sentinel in place and fail this
    assertion, regardless of the developer's shell or CI environment.
    """
    assert os.environ.get("DEP_RANK_TOKEN") is None


@pytest.mark.parametrize(
    ("original", "set_in_body", "expected_after"),
    [
        # Absent originally, test body sets it -> teardown must clear it.
        (None, "set-by-test", None),
        # Present originally, test body changes it -> teardown restores original.
        ("orig-token", "changed-by-test", "orig-token"),
    ],
)
def test_clean_env_teardown_restores_environment(
    original: str | None, set_in_body: str, expected_after: str | None
) -> None:
    """``clean_env`` teardown must restore the pre-test environment exactly.

    A sibling test cannot observe teardown: the next test's autouse ``clean_env``
    setup pops the variable before its body runs, masking any leak. So exercise
    :func:`conftest.isolated_dep_rank_token` (the context manager ``clean_env``
    delegates to) directly and inspect the environment after the block exits.
    Guards the ``original is None`` teardown branch, which a naive
    ``if original is not None: restore`` (no unconditional clear) would leave
    leaking the test-set value.
    """
    if original is None:
        os.environ.pop("DEP_RANK_TOKEN", None)
    else:
        os.environ["DEP_RANK_TOKEN"] = original

    with conftest.isolated_dep_rank_token():
        os.environ["DEP_RANK_TOKEN"] = set_in_body  # noqa: S105 (test value, not a real secret)

    assert os.environ.get("DEP_RANK_TOKEN") == expected_after
    os.environ.pop("DEP_RANK_TOKEN", None)  # leave env clean for the autouse fixture
