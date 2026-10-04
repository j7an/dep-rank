"""Tests for the drift-check evaluation logic and the _run guards (no network I/O).

The _run tests monkeypatch ``scrape_dependents`` so a ``ClientSession`` is created but
never issues a request — the token guard short-circuits before scraping, and the
inconclusive case returns a stubbed result.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import dep_rank.scripts.drift_check as drift_check
from dep_rank.core.models import Repository, ScrapeReason, ScrapeResult


def test_healthy_result_has_no_problems() -> None:
    assert drift_check.evaluate_drift(repos_count=5, total_dependents=15000) == []


def test_zero_repos_flags_item_selectors() -> None:
    problems = drift_check.evaluate_drift(repos_count=0, total_dependents=15000)
    assert any("selector" in p.lower() for p in problems)


def test_zero_total_flags_header() -> None:
    problems = drift_check.evaluate_drift(repos_count=5, total_dependents=0)
    assert any("header" in p.lower() or "count" in p.lower() for p in problems)


def test_both_broken_flags_both() -> None:
    assert len(drift_check.evaluate_drift(repos_count=0, total_dependents=0)) == 2


def test_missing_token_fails_before_scraping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing DRIFT_CHECK_TOKEN exits 2 and never scrapes — no silent unauth run that
    could pass-while-blind. Guards against the 'green check that never checks' failure."""
    monkeypatch.delenv("DRIFT_CHECK_TOKEN", raising=False)

    async def _must_not_scrape(*args: object, **kwargs: object) -> ScrapeResult:
        raise AssertionError("scrape_dependents must not run without a token")

    monkeypatch.setattr(drift_check, "scrape_dependents", _must_not_scrape)
    assert asyncio.run(drift_check._run()) == 2
    assert "DRIFT_CHECK_TOKEN" in capsys.readouterr().err


def _stub(
    *,
    repos: list[Repository],
    pages_scraped: int,
    estimated_total_dependents: int,
    reason: ScrapeReason | None,
) -> ScrapeResult:
    return ScrapeResult(
        repos=repos,
        pages_scraped=pages_scraped,
        max_pages=2,
        estimated_total_pages=pages_scraped,
        estimated_total_dependents=estimated_total_dependents,
        complete=reason is None,
        reason=reason,
        matched_count=len(repos),
    )


HEALTHY_REPO = Repository(owner="a", name="b", url="https://github.com/a/b", stars=900)


@pytest.mark.parametrize(
    ("stub", "code", "stream", "needles"),
    [
        # A transport failure *with* a token is inconclusive: exit 0 (don't page on a
        # flaky GitHub response) but emit a visible ``::warning::`` annotation.
        pytest.param(
            _stub(
                repos=[],
                pages_scraped=0,
                estimated_total_dependents=0,
                reason=ScrapeReason.NETWORK_FAILURE,
            ),
            0,
            "err",
            ("::warning::", "INCONCLUSIVE"),
            id="inconclusive-network",
        ),
        pytest.param(
            _stub(
                repos=[],
                pages_scraped=0,
                estimated_total_dependents=0,
                reason=ScrapeReason.RATE_LIMITED,
            ),
            0,
            "err",
            ("::warning::", "INCONCLUSIVE"),
            id="inconclusive-rate-limited",
        ),
        # A reachable scrape that parses zero repos AND a zero header count is real
        # drift: the path that fails the weekly CI job.
        pytest.param(
            _stub(
                repos=[],
                pages_scraped=2,
                estimated_total_dependents=0,
                reason=ScrapeReason.MAX_PAGES_REACHED,
            ),
            1,
            "err",
            ("DRIFT DETECTED",),
            id="drift",
        ),
        # A reachable, complete scrape is healthy; reason=None also exercises the
        # ``'complete'`` branch of the summary's reason ternary.
        pytest.param(
            _stub(
                repos=[HEALTHY_REPO], pages_scraped=2, estimated_total_dependents=15000, reason=None
            ),
            0,
            "out",
            ("OK:",),
            id="healthy",
        ),
    ],
)
def test_exit_codes(
    stub: ScrapeResult,
    code: int,
    stream: str,
    needles: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DRIFT_CHECK_TOKEN", "ghp_x")
    monkeypatch.setattr(drift_check, "scrape_dependents", AsyncMock(return_value=stub))
    assert asyncio.run(drift_check._run()) == code
    captured = capsys.readouterr()
    text = captured.err if stream == "err" else captured.out
    for needle in needles:
        assert needle in text
