"""Tests for CLI output formatters."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

import pytest
from rich.console import Console

from dep_rank.cli.formatters import (
    _CAUTION_TAGS,
    RetryCountdown,
    build_topk_table,
    console,
    format_scrape_summary,
    humanize,
    partial_warning,
    print_dependents_json,
    print_dependents_table,
    print_search_results,
)
from dep_rank.core.models import (
    CautionCode,
    CautionSignal,
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
    RetryStatus,
    ScrapeReason,
    ScrapeSnapshot,
    TrustCheckResult,
    TrustComponents,
    TrustScore,
)
from tests.conftest import make_repo


def _render(fn: Callable[..., object], *args: object, **kwargs: object) -> str:
    with console.capture() as cap:
        fn(*args, **kwargs)
    return cap.get()


def _flat(text: str) -> str:
    """Collapse whitespace so a wrapped table title reads as one line."""
    return " ".join(text.split())


@pytest.mark.parametrize(
    ("num", "expected"),
    [
        (999, "999"),
        (0, "0"),
        (1500, "1.5K"),
        (9900, "9.9K"),
        (10000, "10K"),
        (82400, "82K"),
        (999999, "999K"),
        (1000000, "1.0M"),
        (1500000, "1.5M"),
        (12345678, "12M"),
    ],
)
def test_humanize(num: int, expected: str) -> None:
    assert humanize(num) == expected


class TestPrintDependentsTable:
    def test_table_with_descriptions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(console, "width", 200)
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=[
                make_repo("alpha", "framework", stars=12500, description="A web framework"),
                make_repo("beta", "toolkit", stars=3200, description=None),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        out = _render(print_dependents_table, result)
        assert "Description" in out
        assert "A web framework" in out


class TestPrintSearchResults:
    def test_no_hits(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=5,
        )
        out = _render(print_search_results, result)
        assert "No results found for 'import os'" in out

    def test_with_hits(self) -> None:
        repo = make_repo("alpha", "framework", stars=5000)
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[
                CodeSearchHit(
                    repo=repo,
                    file_url="https://github.com/alpha/framework/blob/main/app.py",
                    file_path="app.py",
                    matches=3,
                ),
            ],
            searched_repos=1,
        )
        out = _render(print_search_results, result)
        row = next(line for line in out.splitlines() if "alpha/framework" in line)
        assert "app.py" in row
        assert "3" in row  # match count rendered as text


@pytest.mark.parametrize(
    ("kwargs", "must_contain", "must_not_contain"),
    [
        (
            {
                "pages_scraped": 42,
                "max_pages": 1000,
                "estimated_total_pages": 76515,
                "found_count": 387,
                "min_stars": 5,
            },
            [
                "42/1000 pages (4.2%)",
                "42/~76,515 estimated pages (0.05%)",
                "Found 387 dependents with ≥5 stars",
            ],
            [],
        ),
        (
            {
                "pages_scraped": 42,
                "max_pages": 1000,
                "estimated_total_pages": 0,
                "found_count": 387,
                "min_stars": 5,
            },
            ["42/1000 pages (4.2%)", "Found 387 dependents with ≥5 stars"],
            ["estimated"],
        ),
        (
            {
                "pages_scraped": 1000,
                "max_pages": 1000,
                "estimated_total_pages": 76515,
                "found_count": 5000,
                "min_stars": 10,
            },
            ["1000/1000 pages (100.0%)", "1000/~76,515 estimated pages (1.31%)"],
            [],
        ),
        (
            {
                "pages_scraped": 0,
                "max_pages": 1000,
                "estimated_total_pages": 0,
                "found_count": 0,
                "min_stars": 5,
            },
            ["0/1000 pages (0.0%)", "Found 0 dependents"],
            [],
        ),
    ],
    ids=["with-estimate", "without-estimate", "full-scrape", "zero-pages"],
)
def test_format_scrape_summary(
    kwargs: dict[str, int], must_contain: list[str], must_not_contain: list[str]
) -> None:
    summary = format_scrape_summary(**kwargs)
    for text in must_contain:
        assert text in summary
    for text in must_not_contain:
        assert text not in summary


class TestPartialWarning:
    def test_reasons_have_distinct_messages(self) -> None:

        msgs = {
            partial_warning(r)
            for r in (
                ScrapeReason.MAX_PAGES_REACHED,
                ScrapeReason.TREND_CONVERGED,
                ScrapeReason.NETWORK_FAILURE,
                ScrapeReason.RATE_LIMITED,
            )
        }
        assert len(msgs) == 4  # each reason renders a distinct line

    def test_max_pages_mentions_flag(self) -> None:

        assert "--max-pages" in partial_warning(ScrapeReason.MAX_PAGES_REACHED)

    def test_converged_mentions_opt_out(self) -> None:

        assert "--no-adaptive-stop" in partial_warning(ScrapeReason.TREND_CONVERGED)


class TestBuildTopKTable:
    def test_lists_repos_with_humanized_stars(self) -> None:

        snap = ScrapeSnapshot(
            top_k=[
                make_repo("a", "b", stars=1500),
            ],
            pages_scraped=2,
            estimated_total_pages=5,
            estimated_total_dependents=100,
            matched_count=10,
        )
        table = build_topk_table(snap)
        console = Console()
        with console.capture() as cap:
            console.print(table)
        out = cap.get()
        assert "a/b" in out
        assert "1.5K" in out
        assert "10 matched" in out  # progress context in the title
        assert "page 2" in out  # progress context: page number in the title

    def test_empty_top_k_still_renders_progress(self) -> None:
        """A snapshot with no top-K (e.g. high --min-stars early on) must still render
        progress context and a placeholder row, never a blank frame."""

        snap = ScrapeSnapshot(
            top_k=[],
            pages_scraped=3,
            estimated_total_pages=5,
            estimated_total_dependents=100,
            matched_count=0,
        )
        console = Console()
        with console.capture() as cap:
            console.print(build_topk_table(snap))
        out = cap.get()
        assert "page 3" in out
        assert "no matching repositories" in out.lower()


class TestTrustTableAndJson:
    def _trust_repo(self, cautions: list[CautionSignal] | None = None) -> Repository:
        return make_repo(
            "alpha",
            "framework",
            stars=12500,
            trust=TrustScore(
                score=87.4,
                forks=900,
                issues=300,
                pull_requests=120,
                pushed_at=None,
                components=TrustComponents(stars=0.9, forks=0.8, engagement=0.7, recency=0.5),
                cautions=cautions or [],
            ),
        )

    def _result(
        self, *, ranked_by: Literal["stars", "trust"], repos: list[Repository]
    ) -> DependentsResult:
        return DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=repos,
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
            ranked_by=ranked_by,
        )

    def test_trust_table_renders_score_and_stars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(console, "width", 200)  # independent of the test terminal's width
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        out = _render(print_dependents_table, result)
        assert "alpha/framework" in out
        assert "87" in out  # rounded trust score
        assert "12K" in out or "12.5K" in out  # humanized stars
        assert "(by trust)" in _flat(out)
        header = next(line for line in out.splitlines() if "Stars" in line)
        assert header.index("Trust") < header.index("Stars")

    def test_trust_table_footer_explains_pool_relative_score(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        result.trust_pool_size = 100
        out = _flat(_render(print_dependents_table, result))
        assert "relative to the 100 candidates scored" in out
        assert "not comparable across runs" in out

    def test_star_table_has_no_trust_footer(self) -> None:
        result = self._result(ranked_by="stars", repos=[make_repo("beta", "toolkit", stars=3200)])
        assert "candidates scored" not in _flat(_render(print_dependents_table, result))

    def test_fallback_renders_star_table(self) -> None:
        # ranked_by == "stars" even though a star repo has no trust -> star layout.
        star_repo = make_repo("beta", "toolkit", stars=3200)
        result = self._result(ranked_by="stars", repos=[star_repo])
        out = _render(print_dependents_table, result)
        assert "beta/toolkit" in out
        assert "Trust" not in out  # no trust column in star/fallback layout
        assert "(by trust)" not in _flat(out)

    def test_json_star_mode_excludes_trust_and_ranked_by(self) -> None:
        result = self._result(ranked_by="stars", repos=[self._trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=False)
        assert "ranked_by" not in out
        assert "trust" not in out
        assert "trust_signals" not in out

    def test_json_star_mode_preserves_existing_fields_exactly(self) -> None:
        # The printer — not raw model serialization — is the back-compat boundary.
        # Prove pre-trust fields survive unchanged, including an explicit `null`
        # description (the field must remain present, not be dropped by exclude_none).

        repo = make_repo("alpha", "framework", stars=12500, description=None)
        result = self._result(ranked_by="stars", repos=[repo])
        out = _render(print_dependents_json, result, include_rank_metadata=False)
        assert '"description": null' in out  # key present with explicit null
        payload = json.loads(out)
        assert payload["source"] == "https://github.com/django/django"
        assert payload["total_count"] == 100
        assert payload["filtered_count"] == 50
        repo_json = payload["repos"][0]
        assert repo_json == {
            "owner": "alpha",
            "name": "framework",
            "url": "https://github.com/alpha/framework",
            "stars": 12500,
            "description": None,
        }  # exact field set — no trust/trust_signals leakage, nothing dropped
        assert "ranked_by" not in payload

    def test_json_trust_mode_includes_metadata(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert '"ranked_by": "trust"' in out
        assert '"score": 87.4' in out
        assert "trust_signals" not in out  # always excluded structurally

    def test_trust_table_shows_cautions_column_and_footer_when_flagged(self) -> None:
        stale = CautionSignal(code=CautionCode.STALE_ACTIVITY, description="No pushes in 400 days")
        result = self._result(ranked_by="trust", repos=[self._trust_repo([stale])])
        out = _render(print_dependents_table, result)
        assert "Cautions" in out
        assert "stale_activity" not in out  # short tag in the cell, not the JSON code
        assert "no recent pushes" in out  # legend line for the tag
        assert "not evidence of fake stars" in out

    def test_caution_legend_lists_only_present_tags_in_code_order(self) -> None:
        spike = CautionSignal(code=CautionCode.CONCENTRATED_STARRING, description="x")
        young = CautionSignal(code=CautionCode.NEW_WITH_HIGH_STARS, description="y")
        result = self._result(ranked_by="trust", repos=[self._trust_repo([spike, young])])
        out = _render(print_dependents_table, result)
        legend = out[out.index("not evidence of fake stars") :]
        assert legend.index("young") < legend.index("spike")
        assert "archived" not in legend
        assert "stale" not in legend

    def test_multiple_caution_tags_stack_on_separate_lines_in_cell(self) -> None:
        spike = CautionSignal(code=CautionCode.CONCENTRATED_STARRING, description="x")
        young = CautionSignal(code=CautionCode.NEW_WITH_HIGH_STARS, description="y")
        result = self._result(ranked_by="trust", repos=[self._trust_repo([spike, young])])
        out = _render(print_dependents_table, result)
        table_lines = out[: out.index("not evidence of fake stars")].splitlines()
        spike_rows = [i for i, line in enumerate(table_lines) if "spike" in line]
        young_rows = [i for i, line in enumerate(table_lines) if "young" in line]
        assert len(spike_rows) == 1 and len(young_rows) == 1
        assert young_rows[0] == spike_rows[0] + 1  # one tag per line, in signal order

    def test_every_caution_code_has_a_tag(self) -> None:
        assert set(_CAUTION_TAGS) == set(CautionCode)

    def test_trust_table_omits_cautions_column_when_none_flagged(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        out = _render(print_dependents_table, result)
        assert "Cautions" not in out
        assert "not evidence of fake stars" not in out

    def test_json_trust_mode_includes_caution_code_and_description(self) -> None:

        stale = CautionSignal(code=CautionCode.STALE_ACTIVITY, description="No pushes in 400 days")
        result = self._result(ranked_by="trust", repos=[self._trust_repo([stale])])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        payload = json.loads(out)
        assert payload["repos"][0]["trust"]["cautions"] == [
            {"code": "stale_activity", "description": "No pushes in 400 days"}
        ]

    def test_json_trust_mode_without_check_has_no_trust_check_key(self) -> None:

        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert "trust_check" not in json.loads(out)

    def test_json_trust_mode_with_check_includes_it(self) -> None:

        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        result.trust_check = TrustCheckResult(
            complete=False, window_weeks=30, repos_checked=1, unavailable=["alpha/framework"]
        )
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert json.loads(out)["trust_check"]["unavailable"] == ["alpha/framework"]

    @pytest.mark.parametrize("ranked_by", ["stars", "trust"])
    def test_footer_prints_matched_count_once(
        self, ranked_by: Literal["stars", "trust"], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = (
            self._trust_repo() if ranked_by == "trust" else make_repo("beta", "toolkit", stars=3200)
        )
        result = self._result(ranked_by=ranked_by, repos=[repo])
        monkeypatch.setattr(console, "width", 200)
        out = _render(print_dependents_table, result)
        assert out.count("100 dependents at or above the star threshold") == 1
        assert "with stars above threshold" not in out
        assert "total dependents" not in out

    def test_table_footer_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        concentrated = CautionSignal(
            code=CautionCode.CONCENTRATED_STARRING, description="A concentrated day"
        )
        result = self._result(ranked_by="trust", repos=[self._trust_repo([concentrated])])
        result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=1)
        monkeypatch.setattr(console, "width", 200)
        out = _render(print_dependents_table, result)
        assert (
            "Trust check (last 30 weeks of star history): 1 of 1 repos checked · "
            "1 concentrated · 0 insufficient history · 0 unavailable"
        ) in out

    def test_table_footer_reflects_cap(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo() for _ in range(40)])
        result.trust_check = TrustCheckResult(
            complete=True,
            window_weeks=30,
            repos_checked=25,
            insufficient_history=["alpha/framework"],
        )
        out = _render(print_dependents_table, result)
        assert "25 of 40 repos checked" in out
        assert "1 insufficient history" in out

    def test_fallback_star_table_prints_footer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = make_repo("beta", "toolkit", stars=3200)
        result = self._result(ranked_by="stars", repos=[repo])
        result.trust_check = TrustCheckResult(
            complete=False, window_weeks=30, repos_checked=1, unavailable=["beta/toolkit"]
        )
        monkeypatch.setattr(console, "width", 200)
        out = _render(print_dependents_table, result)
        assert "Trust check (last 30 weeks of star history): 1 of 1 repos checked" in out
        assert "0 concentrated · 0 insufficient history · 1 unavailable" in out
        assert out.index("Trust check") > out.index("star threshold")

    def test_no_footer_without_check(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        out = _render(print_dependents_table, result)
        assert "Trust check" not in out


class TestRetryCountdown:
    def test_counts_down_on_each_render(self) -> None:
        clock = [100.0]
        status = RetryStatus(page=8, attempt=1, max_retries=5, delay=120)
        countdown = RetryCountdown(status, now=lambda: clock[0])
        assert str(countdown) == "⏳ GitHub rate limit — resuming in 2:00 (page 8, retry 1/5)"
        clock[0] = 113.2
        assert "resuming in 1:47 " in str(countdown)  # rounds up: never shows 0:00 early
        clock[0] = 500.0
        assert "resuming in 0:00 " in str(countdown)

    def test_is_rich_renderable(self) -> None:
        status = RetryStatus(page=2, attempt=3, max_retries=5, delay=5)
        test_console = Console(width=120, record=True)
        test_console.print(RetryCountdown(status))
        assert "(page 2, retry 3/5)" in test_console.export_text()
