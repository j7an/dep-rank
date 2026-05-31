"""Tests for the drift-check evaluation logic and the _run guards (no network I/O).

The _run tests monkeypatch ``scrape_dependents`` so a ``ClientSession`` is created but
never issues a request — the token guard short-circuits before scraping, and the
inconclusive case returns a stubbed result.
"""

from __future__ import annotations

import asyncio

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


@pytest.mark.parametrize("reason", [ScrapeReason.NETWORK_FAILURE, ScrapeReason.RATE_LIMITED])
def test_transport_failure_is_inconclusive_not_drift(reason: ScrapeReason) -> None:
    """A network/rate failure must not be reported as selector drift, even when the
    failed scrape returned zero repos and a zero header count."""
    assert drift_check.evaluate_drift(repos_count=0, total_dependents=0, reason=reason) == []


def test_max_pages_reached_still_evaluates_selectors() -> None:
    """MAX_PAGES_REACHED is expected on the multi-page canary; it does not suppress
    evaluation — healthy counts pass, and a zero-repo page still flags drift."""
    assert drift_check.evaluate_drift(5, 15000, ScrapeReason.MAX_PAGES_REACHED) == []
    assert drift_check.evaluate_drift(0, 15000, ScrapeReason.MAX_PAGES_REACHED) != []


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


@pytest.mark.parametrize("reason", [ScrapeReason.NETWORK_FAILURE, ScrapeReason.RATE_LIMITED])
def test_inconclusive_with_token_warns_but_exits_zero(
    reason: ScrapeReason,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transport failure *with* a token is inconclusive: exit 0 (don't page on a flaky
    GitHub response) but emit a visible ``::warning::`` annotation, not a silent pass."""
    monkeypatch.setenv("DRIFT_CHECK_TOKEN", "ghp_x")
    stub = ScrapeResult(
        repos=[],
        pages_scraped=0,
        max_pages=2,
        estimated_total_pages=0,
        estimated_total_dependents=0,
        complete=False,
        reason=reason,
        matched_count=0,
    )

    async def _fake_scrape(*args: object, **kwargs: object) -> ScrapeResult:
        return stub

    monkeypatch.setattr(drift_check, "scrape_dependents", _fake_scrape)
    assert asyncio.run(drift_check._run()) == 0
    err = capsys.readouterr().err
    assert "::warning::" in err
    assert "INCONCLUSIVE" in err


def test_drift_detected_with_token_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reachable scrape (non-transport reason) that parses zero repos AND a zero header
    count is real drift: exit 1 with a 'DRIFT DETECTED' message on stderr. This is the
    path that fails the weekly CI job — the canary's whole reason to exist."""
    monkeypatch.setenv("DRIFT_CHECK_TOKEN", "ghp_x")
    stub = ScrapeResult(
        repos=[],
        pages_scraped=2,
        max_pages=2,
        estimated_total_pages=2,
        estimated_total_dependents=0,
        complete=False,
        reason=ScrapeReason.MAX_PAGES_REACHED,
        matched_count=0,
    )

    async def _fake_scrape(*args: object, **kwargs: object) -> ScrapeResult:
        return stub

    monkeypatch.setattr(drift_check, "scrape_dependents", _fake_scrape)
    assert asyncio.run(drift_check._run()) == 1
    assert "DRIFT DETECTED" in capsys.readouterr().err


def test_healthy_scrape_with_token_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reachable, complete scrape with parsed repos and a non-zero header count is
    healthy: exit 0 with an 'OK:' summary on stdout. Uses reason=None to also exercise
    the ``'complete'`` branch of the summary's reason ternary."""
    monkeypatch.setenv("DRIFT_CHECK_TOKEN", "ghp_x")
    stub = ScrapeResult(
        repos=[Repository(owner="a", name="b", url="https://github.com/a/b", stars=900)],
        pages_scraped=2,
        max_pages=2,
        estimated_total_pages=2,
        estimated_total_dependents=15000,
        complete=True,
        reason=None,
        matched_count=1,
    )

    async def _fake_scrape(*args: object, **kwargs: object) -> ScrapeResult:
        return stub

    monkeypatch.setattr(drift_check, "scrape_dependents", _fake_scrape)
    assert asyncio.run(drift_check._run()) == 0
    assert "OK:" in capsys.readouterr().out
