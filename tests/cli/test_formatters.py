"""Tests for CLI output formatters."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from rich.console import Console

from dep_rank.cli.formatters import (
    _CAUTION_TAGS,
    RetryCountdown,
    build_topk_table,
    console,
    downloads_cell,
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
    DownloadsCheckResult,
    PackageDownloads,
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


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render at 200 columns, independent of the test terminal's width."""
    monkeypatch.setattr(console, "width", 200)


def _trust_repo(cautions: list[CautionSignal] | None = None) -> Repository:
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


def _result(*, repos: list[Repository], **fields: Any) -> DependentsResult:
    return DependentsResult(
        **{
            "source": "https://github.com/django/django",
            "total_count": 100,
            "filtered_count": 50,
            "repos": repos,
            "dependent_type": DependentType.REPOSITORY,
            "scraped_at": datetime.now(tz=UTC),
            **fields,
        }
    )


def _snapshot(top_k: list[Repository], **fields: int) -> ScrapeSnapshot:
    counts = {
        "pages_scraped": 1,
        "estimated_total_pages": 1,
        "estimated_total_dependents": len(top_k),
        "matched_count": len(top_k),
    }
    return ScrapeSnapshot(top_k=top_k, **{**counts, **fields})


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


def dl(
    name: str,
    n: int,
    period: str = "last-month",
    verified: bool = False,
    ecosystem: str = "npm",
) -> PackageDownloads:
    return PackageDownloads(
        ecosystem=ecosystem, name=name, downloads=n, period=period, verified=verified
    )


class TestDownloadsColumn:
    @pytest.mark.parametrize(
        ("downloads", "unavailable", "expected"),
        [
            (dl("react", 636140021, verified=True), set(), "636M/mo  npm:react ✓"),
            (dl("next", 280433182), set(), "280M/mo  npm:next"),
            (
                dl("serde", 1505489004, "total", ecosystem="cargo"),
                set(),
                "1505M total  cargo:serde",
            ),
            (dl("x", 1500, "last-week", ecosystem="pypi"), set(), "1.5K (last-week)  pypi:x"),
            (dl("zero", 0), set(), "0/mo  npm:zero"),
            (None, set(), "—"),
            (None, {"o/r"}, "[dim]unavailable[/dim]"),
            (None, {"other/r"}, "—"),
        ],
    )
    def test_downloads_cell(
        self, downloads: PackageDownloads | None, unavailable: set[str], expected: str
    ) -> None:
        assert downloads_cell(make_repo("o", "r", downloads=downloads), unavailable) == expected

    @pytest.mark.parametrize("ranked_by", ["stars", "trust"])
    def test_column_and_legend_only_with_check(self, ranked_by: Literal["stars", "trust"]) -> None:
        result = _result(
            source="https://github.com/o/source",
            total_count=3,
            filtered_count=3,
            repos=[
                make_repo("o", "found", downloads=dl("react", 636140021, verified=True)),
                make_repo("o", "none"),
                make_repo("o", "failed"),
            ],
            ranked_by=ranked_by,
        )
        out = _render(print_dependents_table, result)
        assert "Downloads" not in out
        assert "verified attestation" not in out
        assert "npm:react" not in out
        result.downloads_check = DownloadsCheckResult(complete=False, unavailable=["o/failed"])
        out = _render(print_dependents_table, result)
        header = next(line for line in out.splitlines() if "Stars" in line)
        assert header.index("Stars") < header.index("Downloads")
        assert "636M/mo  npm:react ✓" in out
        assert "—" in next(line for line in out.splitlines() if "o/none" in line)
        assert "unavailable" in next(line for line in out.splitlines() if "o/failed" in line)
        legend = (
            "Downloads: most-downloaded registry package that claims each repo (ecosyste.ms).\n"
            "  ✓  verified attestation links this package to this repo (deps.dev)\n"
            "     unmarked = matched by name only, not verified — check before installing\n"
            "  /mo = last month; total = all time. Not used for ranking."
        )
        assert out.count(legend) == 1
        assert out.index("dependents at or above") < out.index("Downloads:")

    def test_legend_follows_existing_trust_footers(self) -> None:
        repo = _trust_repo([CautionSignal(code=CautionCode.STALE_ACTIVITY, description="x")])
        result = _result(ranked_by="trust", repos=[repo])
        result.downloads_check = DownloadsCheckResult(complete=True)
        result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=1)
        out = _render(print_dependents_table, result)
        header = next(line for line in out.splitlines() if "Stars" in line)
        assert header.index("Stars") < header.index("Downloads") < header.index("Cautions")
        assert out.index("Trust scores rank") < out.index("Cautions (informational")
        assert out.index("Cautions (informational") < out.index("Trust check (last")
        assert out.index("Trust check (last") < out.index("Downloads:")

    @pytest.mark.parametrize("field", ["name", "ecosystem", "period"])
    def test_third_party_markup_renders_literally_without_links(
        self, field: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        injection = "[link=https://evil.example]x[/link]"
        package = dl("react", 1500).model_copy(update={field: injection})
        terminal = Console(width=400, force_terminal=True, color_system="truecolor")
        monkeypatch.setattr("dep_rank.cli.formatters.console", terminal)
        result = _result(
            source="https://github.com/o/source",
            total_count=1,
            filtered_count=1,
            repos=[make_repo("o", "r", downloads=package)],
            downloads_check=DownloadsCheckResult(complete=True),
        )
        with terminal.capture() as cap:
            print_dependents_table(result)
        out = cap.get()
        assert injection in _flat(out)
        assert ";https://evil.example" not in out  # no OSC 8 hyperlink to the injected URL


def _terminal_render(
    fn: Callable[..., object], monkeypatch: pytest.MonkeyPatch, arg: object
) -> str:
    # Pin console capabilities: Rich drops OSC 8 links under auto-detected TERM=dumb
    # (no color system) and on Windows consoles without VT support (Windows CI).
    terminal = Console(
        width=200, force_terminal=True, color_system="truecolor", legacy_windows=False
    )
    monkeypatch.setattr("dep_rank.cli.formatters.console", terminal)
    with terminal.capture() as cap:
        fn(arg)
    return cap.get()


class TestPrintDependentsTable:
    def test_repo_name_links_to_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _result(total_count=1, filtered_count=1, repos=[make_repo("stablyai", "orca")])
        out = _terminal_render(print_dependents_table, monkeypatch, result)
        assert ";https://github.com/stablyai/orca\x1b\\" in out

    def test_table_with_descriptions(self) -> None:
        result = _result(
            repos=[
                make_repo("alpha", "framework", stars=12500, description="A web framework"),
                make_repo("beta", "toolkit", stars=3200, description=None),
            ]
        )
        out = _render(print_dependents_table, result)
        assert "Description" in out
        assert "A web framework" in out

    def test_external_strings_render_literally(self) -> None:
        # A dependent's owner controls its description; the source URL's query is user input.
        description = "[link=https://evil.example]docs[/link] [bold red]VERIFIED[/]"
        result = _result(
            source="https://github.com/django/django?x=[red]y",
            total_count=1,
            filtered_count=1,
            repos=[make_repo("alpha", "framework", description=description)],
        )
        out = _flat(_render(print_dependents_table, result))
        assert description in out
        assert "https://github.com/django/django?x=[red]y" in out


class TestPrintSearchResults:
    def test_repo_name_links_to_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hit = CodeSearchHit(
            repo=make_repo("stablyai", "orca"),
            file_url="https://github.com/stablyai/orca/blob/main/app.py",
            file_path="app.py",
            matches=1,
        )
        result = CodeSearchResult(
            source="https://github.com/django/django", query="q", hits=[hit], searched_repos=1
        )
        out = _terminal_render(print_search_results, monkeypatch, result)
        assert ";https://github.com/stablyai/orca\x1b\\" in out

    def test_no_hits(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=5,
        )
        out = _render(print_search_results, result)
        assert "No results found for 'import os'" in out
        assert "not searched" not in out
        assert "may be incomplete" not in out

    def test_no_hits_warns_about_failed_repos(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="q",
            hits=[],
            searched_repos=0,
            failed_repos={"a/b": "ConnectError", "c/d": "HTTP 401", "e/f": "HTTP 401"},
        )
        out = _flat(_render(print_search_results, result))
        assert "No results found for 'q'" in out
        assert "⚠ 3 repos not searched: HTTP 401 (2), ConnectError (1)" in out

    def test_hits_warn_about_failed_repos(self) -> None:
        hit = CodeSearchHit(
            repo=make_repo("alpha", "framework"),
            file_url="https://github.com/alpha/framework/blob/main/app.py",
            file_path="app.py",
            matches=1,
        )
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="q",
            hits=[hit],
            searched_repos=1,
            failed_repos={"c/d": "HTTP 403"},
        )
        out = _flat(_render(print_search_results, result))
        assert "⚠ 1 repos not searched: HTTP 403 (1)" in out

    def test_no_hits_warns_about_failed_and_incomplete_repos(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="q",
            hits=[],
            searched_repos=2,
            failed_repos={"c/d": "HTTP 403"},
            incomplete_repos=["a/b", "e/f"],
        )
        out = _flat(_render(print_search_results, result))
        assert "⚠ 2 repos may be incomplete (GitHub search timed out): a/b, e/f" in out
        assert out.index("not searched") < out.index("may be incomplete")

    def test_hits_warn_about_incomplete_repos(self) -> None:
        hit = CodeSearchHit(
            repo=make_repo("alpha", "framework"),
            file_url="https://github.com/alpha/framework/blob/main/app.py",
            file_path="app.py",
            matches=1,
        )
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="q",
            hits=[hit],
            searched_repos=1,
            incomplete_repos=["alpha/framework"],
        )
        out = _flat(_render(print_search_results, result))
        assert "⚠ 1 repos may be incomplete (GitHub search timed out): alpha/framework" in out

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
        assert "not searched" not in out
        assert "may be incomplete" not in out

    def test_brackets_render_literally(self) -> None:
        hit = CodeSearchHit(
            repo=make_repo("alpha", "framework"),
            file_url="https://github.com/alpha/framework/blob/main/pages/[id].tsx",
            file_path="pages/[id].tsx",
            matches=1,
        )
        result = CodeSearchResult(
            source="https://github.com/django/django", query="[red]x", hits=[hit], searched_repos=1
        )
        out = _render(print_search_results, result)
        assert "Code search: '[red]x'" in _flat(out)
        assert "pages/[id].tsx" in out

    def test_no_hits_query_renders_literally(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django", query="[red]x", hits=[], searched_repos=5
        )
        assert "No results found for '[red]x'" in _render(print_search_results, result)


def test_json_preserves_strings_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    # 80 columns is the piped-stdout width; markup, emoji codes, and long lines must all survive.
    monkeypatch.setattr(console, "width", 80)
    description = "Ship it :rocket: [bold]fast[/bold] " + "word " * 60 + "C:\\path\\"
    result = _result(
        total_count=1,
        filtered_count=1,
        repos=[make_repo("alpha", "framework", description=description)],
    )
    payload = json.loads(_render(print_dependents_json, result))
    assert payload["repos"][0]["description"] == description


def _summary(
    pages: int, max_pages: int, est_pages: int, found: int, min_stars: int, complete: bool
) -> dict[str, Any]:
    return {
        "pages_scraped": pages,
        "max_pages": max_pages,
        "estimated_total_pages": est_pages,
        "found_count": found,
        "min_stars": min_stars,
        "complete": complete,
    }


@pytest.mark.parametrize(
    ("kwargs", "must_contain", "must_not_contain"),
    [
        pytest.param(
            _summary(42, 1000, 76515, 387, 5, complete=False),
            [
                "42/1000 pages (4.2%)",
                "42/~76,515 estimated pages (0.05%)",
                "Found 387 dependents with ≥5 stars",
            ],
            [],
            id="with-estimate",
        ),
        pytest.param(
            _summary(42, 1000, 0, 387, 5, complete=False),
            ["42/1000 pages (4.2%)", "Found 387 dependents with ≥5 stars"],
            ["estimated"],
            id="without-estimate",
        ),
        pytest.param(
            _summary(1000, 1000, 76515, 5000, 10, complete=False),
            ["1000/1000 pages (100.0%)", "1000/~76,515 estimated pages (1.31%)"],
            [],
            id="full-scrape",
        ),
        pytest.param(
            _summary(0, 1000, 0, 0, 5, complete=False),
            ["0/1000 pages (0.0%)", "Found 0 dependents"],
            [],
            id="zero-pages",
        ),
        pytest.param(
            _summary(3, 200, 3, 42, 5, complete=True),
            ["Scraped all 3 pages", "Found 42 dependents with ≥5 stars"],
            ["%", "/200", "estimated"],
            id="complete-multi-page",
        ),
        pytest.param(
            _summary(1, 20, 0, 2, 5, complete=True),
            ["Scraped 1 page ·"],
            ["%", "all"],
            id="complete-single-page",
        ),
    ],
)
def test_format_scrape_summary(
    kwargs: dict[str, Any], must_contain: list[str], must_not_contain: list[str]
) -> None:
    summary = format_scrape_summary(**kwargs)
    for text in must_contain:
        assert text in summary
    for text in must_not_contain:
        assert text not in summary


class TestPartialWarning:
    def test_reasons_have_distinct_messages(self) -> None:

        msgs = {partial_warning(r) for r in ScrapeReason}
        assert len(msgs) == len(ScrapeReason)  # each reason renders a distinct line

    def test_max_pages_mentions_flag(self) -> None:

        assert "--max-pages" in partial_warning(ScrapeReason.MAX_PAGES_REACHED)

    def test_converged_mentions_opt_out(self) -> None:

        assert "--no-adaptive-stop" in partial_warning(ScrapeReason.TREND_CONVERGED)


class TestBuildTopKTable:
    def test_repo_name_links_to_github(self) -> None:
        snap = _snapshot([make_repo("stablyai", "orca")])
        console = Console(force_terminal=True, color_system="truecolor", legacy_windows=False)
        with console.capture() as cap:
            console.print(build_topk_table(snap))
        assert ";https://github.com/stablyai/orca\x1b\\" in cap.get()

    def test_repo_link_excludes_column_padding(self) -> None:
        # "a/b" is padded to the width of "stablyai/orca"; only the name may be clickable.
        snap = _snapshot([make_repo("stablyai", "orca"), make_repo("a", "b")])
        console = Console(force_terminal=True, color_system="truecolor", legacy_windows=False)
        with console.capture() as cap:
            console.print(build_topk_table(snap))
        link = re.search(r"\x1b\]8;[^;]*;https://github\.com/a/b\x1b\\(.*?)\x1b\]8;;", cap.get())
        assert link is not None
        assert re.sub(r"\x1b\[[0-9;]*m", "", link.group(1)) == "a/b"

    def test_lists_repos_with_humanized_stars(self) -> None:

        snap = _snapshot(
            [make_repo("a", "b", stars=1500)],
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

        snap = _snapshot(
            [], pages_scraped=3, estimated_total_pages=5, estimated_total_dependents=100
        )
        console = Console()
        with console.capture() as cap:
            console.print(build_topk_table(snap))
        out = cap.get()
        assert "page 3" in out
        assert "no matching repositories" in out.lower()


class TestTrustTableAndJson:
    def test_trust_table_renders_score_and_stars(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo()])
        out = _render(print_dependents_table, result)
        assert "alpha/framework" in out
        assert "87" in out  # rounded trust score
        assert "12K" in out or "12.5K" in out  # humanized stars
        assert "(by trust)" in _flat(out)
        header = next(line for line in out.splitlines() if "Stars" in line)
        assert header.index("Trust") < header.index("Stars")

    def test_trust_footer_when_pool_larger_than_shown_rows(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo(), _trust_repo()])
        result.trust_pool_size = 100
        out = _flat(_render(print_dependents_table, result))
        assert (
            "Trust scores rank the 100 most-starred dependents against each other; "
            "the 2 rows above are the top of that ranking." in out
        )
        assert "100 = strongest on every signal; not comparable across runs." in out

    def test_trust_footer_when_every_scored_repo_is_shown(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo(), _trust_repo()])
        result.trust_pool_size = 2
        out = _flat(_render(print_dependents_table, result))
        assert "Trust scores rank these 2 rows against each other." in out
        assert "most-starred" not in out
        assert "100 = strongest on every signal; not comparable across runs." in out

    def test_star_table_has_no_trust_footer(self) -> None:
        result = _result(ranked_by="stars", repos=[make_repo("beta", "toolkit", stars=3200)])
        assert "Trust scores rank" not in _flat(_render(print_dependents_table, result))

    def test_fallback_renders_star_table(self) -> None:
        # ranked_by == "stars" even though a star repo has no trust -> star layout.
        star_repo = make_repo("beta", "toolkit", stars=3200)
        result = _result(ranked_by="stars", repos=[star_repo])
        out = _render(print_dependents_table, result)
        assert "beta/toolkit" in out
        assert "Trust" not in out  # no trust column in star/fallback layout
        assert "(by trust)" not in _flat(out)

    def test_json_star_mode_excludes_trust_and_ranked_by(self) -> None:
        result = _result(ranked_by="stars", repos=[_trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=False)
        assert "ranked_by" not in out
        assert "trust" not in out
        assert "trust_signals" not in out

    def test_json_star_mode_preserves_existing_fields_exactly(self) -> None:
        # The printer — not raw model serialization — is the back-compat boundary.
        # Prove pre-trust fields survive unchanged, including an explicit `null`
        # description (the field must remain present, not be dropped by exclude_none).

        repo = make_repo("alpha", "framework", stars=12500, description=None)
        result = _result(ranked_by="stars", repos=[repo])
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

    @pytest.mark.parametrize(
        ("ranked_by", "trust_check"), [("stars", False), ("trust", False), ("trust", True)]
    )
    def test_json_without_downloads_check_has_no_downloads_keys(
        self, ranked_by: Literal["stars", "trust"], trust_check: bool
    ) -> None:
        result = _result(ranked_by=ranked_by, repos=[_trust_repo()])
        if trust_check:
            result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=1)
        out = _render(print_dependents_json, result, include_rank_metadata=ranked_by == "trust")
        assert "downloads" not in out

    @pytest.mark.parametrize(
        ("ranked_by", "trust_check"), [("stars", False), ("trust", False), ("trust", True)]
    )
    def test_json_with_downloads_check_includes_rows_and_check(
        self, ranked_by: Literal["stars", "trust"], trust_check: bool
    ) -> None:
        found = make_repo(
            "alpha",
            "framework",
            downloads=PackageDownloads(
                ecosystem="npm",
                name="react",
                downloads=636140021,
                period="last-month",
                verified=True,
            ),
        )
        result = _result(ranked_by=ranked_by, repos=[found, make_repo("beta", "toolkit")])
        if trust_check:
            result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=2)
        result.downloads_check = DownloadsCheckResult(complete=False, unavailable=["beta/toolkit"])
        payload = json.loads(
            _render(print_dependents_json, result, include_rank_metadata=ranked_by == "trust")
        )
        assert payload["repos"][0]["downloads"] == {
            "ecosystem": "npm",
            "name": "react",
            "downloads": 636140021,
            "period": "last-month",
            "verified": True,
        }
        assert payload["repos"][1]["downloads"] is None
        assert payload["downloads_check"] == {"complete": False, "unavailable": ["beta/toolkit"]}

    @pytest.mark.parametrize("downloads_check", [False, True])
    @pytest.mark.parametrize(
        ("rank_metadata", "trust_check"), [(False, False), (True, False), (True, True)]
    )
    def test_json_key_sets_per_mode(
        self, rank_metadata: bool, trust_check: bool, downloads_check: bool
    ) -> None:
        result = _result(ranked_by="trust" if rank_metadata else "stars", repos=[_trust_repo()])
        if trust_check:
            result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=1)
        if downloads_check:
            result.downloads_check = DownloadsCheckResult(complete=True)
        payload = json.loads(
            _render(print_dependents_json, result, include_rank_metadata=rank_metadata)
        )
        top = {
            "source",
            "total_count",
            "filtered_count",
            "repos",
            "dependent_type",
            "scraped_at",
            "complete",
            "reason",
            "pages_scraped",
            "estimated_total_pages",
        }
        repo = {"owner", "name", "url", "stars", "description"}
        if rank_metadata:
            top.add("ranked_by")
            repo.add("trust")
        if trust_check:
            top.add("trust_check")
        if downloads_check:
            top.add("downloads_check")
            repo.add("downloads")
        assert set(payload) == top
        assert set(payload["repos"][0]) == repo

    def test_json_trust_mode_includes_metadata(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert '"ranked_by": "trust"' in out
        assert '"score": 87.4' in out
        assert "trust_signals" not in out  # always excluded structurally

    def test_trust_table_shows_cautions_column_and_footer_when_flagged(self) -> None:
        stale = CautionSignal(code=CautionCode.STALE_ACTIVITY, description="No pushes in 400 days")
        result = _result(ranked_by="trust", repos=[_trust_repo([stale])])
        out = _render(print_dependents_table, result)
        assert "Cautions" in out
        assert "stale_activity" not in out  # short tag in the cell, not the JSON code
        assert "no recent pushes" in out  # legend line for the tag
        assert "not evidence of fake stars" in out

    def test_caution_legend_lists_only_present_tags_in_code_order(self) -> None:
        spike = CautionSignal(code=CautionCode.CONCENTRATED_STARRING, description="x")
        young = CautionSignal(code=CautionCode.NEW_WITH_HIGH_STARS, description="y")
        result = _result(ranked_by="trust", repos=[_trust_repo([spike, young])])
        out = _render(print_dependents_table, result)
        legend = out[out.index("not evidence of fake stars") :]
        assert legend.index("young") < legend.index("spike")
        assert "archived" not in legend
        assert "stale" not in legend

    def test_multiple_caution_tags_stack_on_separate_lines_in_cell(self) -> None:
        spike = CautionSignal(code=CautionCode.CONCENTRATED_STARRING, description="x")
        young = CautionSignal(code=CautionCode.NEW_WITH_HIGH_STARS, description="y")
        result = _result(ranked_by="trust", repos=[_trust_repo([spike, young])])
        out = _render(print_dependents_table, result)
        table_lines = out[: out.index("not evidence of fake stars")].splitlines()
        spike_rows = [i for i, line in enumerate(table_lines) if "spike" in line]
        young_rows = [i for i, line in enumerate(table_lines) if "young" in line]
        assert len(spike_rows) == 1 and len(young_rows) == 1
        assert young_rows[0] == spike_rows[0] + 1  # one tag per line, in signal order

    def test_every_caution_code_has_a_tag(self) -> None:
        assert set(_CAUTION_TAGS) == set(CautionCode)

    def test_trust_table_omits_cautions_column_when_none_flagged(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo()])
        out = _render(print_dependents_table, result)
        assert "Cautions" not in out
        assert "not evidence of fake stars" not in out

    def test_json_trust_mode_includes_caution_code_and_description(self) -> None:

        stale = CautionSignal(code=CautionCode.STALE_ACTIVITY, description="No pushes in 400 days")
        result = _result(ranked_by="trust", repos=[_trust_repo([stale])])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        payload = json.loads(out)
        assert payload["repos"][0]["trust"]["cautions"] == [
            {"code": "stale_activity", "description": "No pushes in 400 days"}
        ]

    def test_json_trust_mode_without_check_has_no_trust_check_key(self) -> None:

        result = _result(ranked_by="trust", repos=[_trust_repo()])
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert "trust_check" not in json.loads(out)

    def test_json_trust_mode_with_check_includes_it(self) -> None:

        result = _result(ranked_by="trust", repos=[_trust_repo()])
        result.trust_check = TrustCheckResult(
            complete=False, window_weeks=30, repos_checked=1, unavailable=["alpha/framework"]
        )
        out = _render(print_dependents_json, result, include_rank_metadata=True)
        assert json.loads(out)["trust_check"]["unavailable"] == ["alpha/framework"]

    @pytest.mark.parametrize("ranked_by", ["stars", "trust"])
    def test_footer_prints_matched_count_once(self, ranked_by: Literal["stars", "trust"]) -> None:
        repo = _trust_repo() if ranked_by == "trust" else make_repo("beta", "toolkit", stars=3200)
        result = _result(ranked_by=ranked_by, repos=[repo])
        out = _render(print_dependents_table, result)
        assert out.count("100 dependents at or above the star threshold") == 1
        assert "with stars above threshold" not in out
        assert "total dependents" not in out

    def test_table_footer_counts(self) -> None:
        concentrated = CautionSignal(
            code=CautionCode.CONCENTRATED_STARRING, description="A concentrated day"
        )
        result = _result(ranked_by="trust", repos=[_trust_repo([concentrated])])
        result.trust_check = TrustCheckResult(complete=True, window_weeks=30, repos_checked=1)
        out = _render(print_dependents_table, result)
        assert (
            "Trust check (last 30 weeks of star history): 1 of 1 repos checked · "
            "1 concentrated · 0 insufficient history · 0 unavailable"
        ) in out

    def test_table_footer_reflects_cap(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo() for _ in range(40)])
        result.trust_check = TrustCheckResult(
            complete=True,
            window_weeks=30,
            repos_checked=25,
            insufficient_history=["alpha/framework"],
        )
        out = _render(print_dependents_table, result)
        assert "25 of 40 repos checked" in out
        assert "1 insufficient history" in out

    def test_fallback_star_table_prints_footer(self) -> None:
        repo = make_repo("beta", "toolkit", stars=3200)
        result = _result(ranked_by="stars", repos=[repo])
        result.trust_check = TrustCheckResult(
            complete=False, window_weeks=30, repos_checked=1, unavailable=["beta/toolkit"]
        )
        out = _render(print_dependents_table, result)
        assert "Trust check (last 30 weeks of star history): 1 of 1 repos checked" in out
        assert "0 concentrated · 0 insufficient history · 1 unavailable" in out
        assert out.index("Trust check") > out.index("star threshold")

    def test_no_footer_without_check(self) -> None:
        result = _result(ranked_by="trust", repos=[_trust_repo()])
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
