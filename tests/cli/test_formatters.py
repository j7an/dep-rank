"""Tests for CLI output formatters."""

from __future__ import annotations

from datetime import UTC, datetime

from dep_rank.cli.formatters import (
    console,
    format_scrape_summary,
    humanize,
    print_dependents_json,
    print_dependents_table,
    print_search_results,
)
from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
    TrustComponents,
    TrustScore,
)


class TestHumanize:
    def test_under_1000(self) -> None:
        assert humanize(999) == "999"
        assert humanize(0) == "0"

    def test_thousands(self) -> None:
        assert humanize(1500) == "1.5K"
        assert humanize(9900) == "9.9K"

    def test_ten_thousands(self) -> None:
        assert humanize(10000) == "10K"
        assert humanize(82400) == "82K"
        assert humanize(999999) == "999K"

    def test_millions(self) -> None:
        assert humanize(1000000) == "1.0M"
        assert humanize(1500000) == "1.5M"
        assert humanize(12345678) == "12M"


class TestPrintDependentsTable:
    def test_basic_table(self) -> None:
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=12500,
                ),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        # Should not raise
        print_dependents_table(result)

    def test_table_with_descriptions(self) -> None:
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=12500,
                    description="A web framework",
                ),
                Repository(
                    owner="beta",
                    name="toolkit",
                    url="https://github.com/beta/toolkit",
                    stars=3200,
                    description=None,
                ),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        # Should not raise — exercises the has_descriptions branch
        print_dependents_table(result)


class TestPrintSearchResults:
    def test_no_hits(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=5,
        )
        # Should not raise — exercises the "no results" branch
        print_search_results(result)

    def test_with_hits(self) -> None:
        repo = Repository(
            owner="alpha",
            name="framework",
            url="https://github.com/alpha/framework",
            stars=5000,
        )
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
        # Should not raise — exercises the table building with hits
        print_search_results(result)


class TestFormatScrapeSummary:
    def test_with_estimated_total(self) -> None:
        summary = format_scrape_summary(
            pages_scraped=42,
            max_pages=1000,
            estimated_total_pages=76515,
            found_count=387,
            min_stars=5,
        )
        assert "42/1000 pages (4.2%)" in summary
        assert "42/~76,515 estimated pages (0.05%)" in summary
        assert "Found 387 dependents with ≥5 stars" in summary

    def test_without_estimated_total(self) -> None:
        summary = format_scrape_summary(
            pages_scraped=42,
            max_pages=1000,
            estimated_total_pages=0,
            found_count=387,
            min_stars=5,
        )
        assert "42/1000 pages (4.2%)" in summary
        assert "estimated" not in summary
        assert "Found 387 dependents with ≥5 stars" in summary

    def test_full_scrape(self) -> None:
        summary = format_scrape_summary(
            pages_scraped=1000,
            max_pages=1000,
            estimated_total_pages=76515,
            found_count=5000,
            min_stars=10,
        )
        assert "1000/1000 pages (100.0%)" in summary
        assert "1000/~76,515 estimated pages (1.31%)" in summary

    def test_zero_pages(self) -> None:
        summary = format_scrape_summary(
            pages_scraped=0,
            max_pages=1000,
            estimated_total_pages=0,
            found_count=0,
            min_stars=5,
        )
        assert "0/1000 pages (0.0%)" in summary
        assert "Found 0 dependents" in summary


class TestPartialWarning:
    def test_reasons_have_distinct_messages(self) -> None:
        from dep_rank.cli.formatters import partial_warning
        from dep_rank.core.models import ScrapeReason

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
        from dep_rank.cli.formatters import partial_warning
        from dep_rank.core.models import ScrapeReason

        assert "--max-pages" in partial_warning(ScrapeReason.MAX_PAGES_REACHED)

    def test_converged_mentions_opt_out(self) -> None:
        from dep_rank.cli.formatters import partial_warning
        from dep_rank.core.models import ScrapeReason

        assert "--no-adaptive-stop" in partial_warning(ScrapeReason.TREND_CONVERGED)


class TestBuildTopKTable:
    def test_lists_repos_with_humanized_stars(self) -> None:
        from rich.console import Console

        from dep_rank.cli.formatters import build_topk_table
        from dep_rank.core.models import Repository, ScrapeSnapshot

        snap = ScrapeSnapshot(
            top_k=[
                Repository(owner="a", name="b", url="https://github.com/a/b", stars=1500),
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
        from rich.console import Console

        from dep_rank.cli.formatters import build_topk_table
        from dep_rank.core.models import ScrapeSnapshot

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
    def _trust_repo(self) -> Repository:
        return Repository(
            owner="alpha",
            name="framework",
            url="https://github.com/alpha/framework",
            stars=12500,
            trust=TrustScore(
                score=87.4,
                forks=900,
                issues=300,
                pull_requests=120,
                pushed_at=None,
                components=TrustComponents(stars=0.9, forks=0.8, engagement=0.7, recency=0.5),
            ),
        )

    def _result(self, *, ranked_by: str, repos: list[Repository]) -> DependentsResult:
        return DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=repos,
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
            ranked_by=ranked_by,
        )

    def test_trust_table_renders_score_and_stars(self) -> None:
        result = self._result(ranked_by="trust", repos=[self._trust_repo()])
        with console.capture() as cap:
            print_dependents_table(result)
        out = cap.get()
        assert "alpha/framework" in out
        assert "87" in out  # rounded trust score
        assert "12K" in out or "12.5K" in out  # humanized stars

    def test_fallback_renders_star_table(self) -> None:
        # ranked_by == "stars" even though a star repo has no trust -> star layout.
        star_repo = Repository(
            owner="beta", name="toolkit", url="https://github.com/beta/toolkit", stars=3200
        )
        result = self._result(ranked_by="stars", repos=[star_repo])
        with console.capture() as cap:
            print_dependents_table(result)
        out = cap.get()
        assert "beta/toolkit" in out
        assert "Trust" not in out  # no trust column in star/fallback layout

    def test_json_star_mode_excludes_trust_and_ranked_by(self) -> None:
        result = self._result(ranked_by="stars", repos=[self._trust_repo()])
        with console.capture() as cap:
            print_dependents_json(result, include_rank_metadata=False)
        out = cap.get()
        assert "ranked_by" not in out
        assert "trust" not in out
        assert "trust_signals" not in out

    def test_json_star_mode_preserves_existing_fields_exactly(self) -> None:
        # The printer — not raw model serialization — is the back-compat boundary.
        # Prove pre-trust fields survive unchanged, including an explicit `null`
        # description (the field must remain present, not be dropped by exclude_none).
        import json

        repo = Repository(
            owner="alpha",
            name="framework",
            url="https://github.com/alpha/framework",
            stars=12500,
            description=None,  # must serialize as "description": null, not vanish
        )
        result = self._result(ranked_by="stars", repos=[repo])
        with console.capture() as cap:
            print_dependents_json(result, include_rank_metadata=False)
        out = cap.get()
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
        with console.capture() as cap:
            print_dependents_json(result, include_rank_metadata=True)
        out = cap.get()
        assert '"ranked_by": "trust"' in out
        assert '"score": 87.4' in out
        assert "trust_signals" not in out  # always excluded structurally
