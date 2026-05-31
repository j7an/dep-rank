"""Tests for CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from dep_rank import __version__
from dep_rank.cli.app import cli
from dep_rank.core.models import DependentsResult, DependentType, Repository, ScrapeResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_result() -> DependentsResult:
    return DependentsResult(
        source="https://github.com/django/django",
        total_count=3000,
        filtered_count=150,
        repos=[
            Repository(
                owner="alpha",
                name="framework",
                url="https://github.com/alpha/framework",
                stars=12500,
            ),
            Repository(
                owner="beta", name="toolkit", url="https://github.com/beta/toolkit", stars=3200
            ),
        ],
        dependent_type=DependentType.REPOSITORY,
        scraped_at=datetime.now(tz=UTC),
    )


class TestDepsCommand:
    def test_missing_url(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps"])
        assert result.exit_code != 0

    @patch("dep_rank.cli.app.run_deps")
    def test_basic_invocation(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(cli, ["deps", "https://github.com/django/django"])
        assert result.exit_code == 0

    @patch("dep_rank.cli.app.run_deps")
    def test_json_output(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(
            cli, ["deps", "https://github.com/django/django", "--format", "json"]
        )
        assert result.exit_code == 0
        assert "alpha" in result.output

    def test_invalid_url(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps", "https://gitlab.com/foo/bar"])
        assert result.exit_code != 0


class TestSearchCommand:
    def test_missing_token(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["search", "https://github.com/django/django", "import os"])
        assert result.exit_code != 0


class TestCacheCommand:
    def test_cache_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "clear" in result.output or "stats" in result.output


class TestDepsCommandFull:
    """Tests that exercise the full deps command body with mocked core functions."""

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_table_output(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.return_value = ScrapeResult(
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=12500,
                ),
            ],
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        result = runner.invoke(cli, ["deps", "https://github.com/django/django"])
        assert result.exit_code == 0
        assert "alpha" in result.output

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.graphql.enrich_with_graphql", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_with_descriptions(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        repos = [
            Repository(
                owner="alpha",
                name="framework",
                url="https://github.com/alpha/framework",
                stars=12500,
                description="A framework",
            ),
        ]
        mock_scrape.return_value = ScrapeResult(
            repos=repos,
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        mock_enrich.return_value = repos
        result = runner.invoke(
            cli,
            [
                "deps",
                "https://github.com/django/django",
                "--descriptions",
                "--token",
                "test-token",
            ],
        )
        assert result.exit_code == 0
        assert "alpha" in result.output

    def test_deps_descriptions_without_token(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps", "https://github.com/django/django", "--descriptions"])
        assert result.exit_code != 0
        assert "requires a GitHub token" in result.output


class TestSearchCommandFull:
    """Tests that exercise the full search command body."""

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_full(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import CodeSearchResult

        mock_scrape.return_value = ScrapeResult(
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=5000,
                ),
            ],
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=1,
        )
        result = runner.invoke(
            cli,
            ["search", "https://github.com/django/django", "import os", "--token", "test-token"],
        )
        assert result.exit_code == 0

    def test_search_invalid_url(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["search", "https://gitlab.com/foo/bar", "import os", "--token", "test-token"],
        )
        assert result.exit_code != 0
        assert "Error" in result.output


class TestCacheCommandsFull:
    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.clear", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    def test_cache_clear(
        self,
        mock_init: AsyncMock,
        mock_clear: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        result = runner.invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Cache cleared" in result.output

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.stats", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    def test_cache_stats(
        self,
        mock_init: AsyncMock,
        mock_stats: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_stats.return_value = {"entries": 42, "size_bytes": 12345}
        result = runner.invoke(cli, ["cache", "stats"])
        assert result.exit_code == 0
        assert "42" in result.output
        assert "12,345" in result.output


class TestVersionOption:
    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestDepsHardeningFlags:
    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_flags_pass_through_and_counts_use_matched(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import ScrapeReason

        mock_scrape.return_value = ScrapeResult(
            repos=[Repository(owner="a", name="b", url="https://github.com/a/b", stars=900)],
            pages_scraped=200,
            max_pages=200,
            estimated_total_pages=500,
            estimated_total_dependents=15000,
            complete=False,
            reason=ScrapeReason.MAX_PAGES_REACHED,
            matched_count=4200,
        )
        result = runner.invoke(
            cli,
            [
                "deps",
                "https://github.com/django/django",
                "--token",
                "ghp_x",
                "--concurrency",
                "5",
                "--no-adaptive-stop",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        _, kwargs = mock_scrape.call_args
        assert kwargs["concurrency"] == 5
        assert kwargs["adaptive_stop"] is False
        assert kwargs["rows"] == 10  # default --rows
        import json

        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert payload["reason"] == "max_pages_reached"
        assert payload["total_count"] == 4200
        assert "Scraped" not in result.stdout
        assert "Found" not in result.stdout
        assert "⚠" not in result.stdout

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_table_mode_shows_summary(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.return_value = ScrapeResult(
            repos=[Repository(owner="a", name="b", url="https://github.com/a/b", stars=900)],
            pages_scraped=3,
            max_pages=200,
            estimated_total_pages=3,
            estimated_total_dependents=90,
            matched_count=1,
        )
        result = runner.invoke(
            cli, ["deps", "https://github.com/django/django", "--token", "ghp_x"]
        )
        assert result.exit_code == 0
        assert "Found" in result.output  # summary shown in table mode

    @patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock)
    def test_unauthenticated_warning(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(cli, ["deps", "https://github.com/django/django"])
        assert result.exit_code == 0
        assert "token" in result.stderr.lower()
        assert "60" in result.stderr  # mentions the 60/hour limit

    @patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock)
    def test_no_warning_with_token(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(
            cli, ["deps", "https://github.com/django/django", "--token", "ghp_x"]
        )
        assert result.exit_code == 0
        assert "token" not in result.stderr.lower()

    def test_concurrency_out_of_range_is_rejected(self, runner: CliRunner) -> None:
        for bad in ("0", "11"):
            result = runner.invoke(
                cli,
                ["deps", "https://github.com/x/y", "--token", "ghp_x", "--concurrency", bad],
            )
            assert result.exit_code != 0  # Click IntRange usage error
            assert "concurrency" in result.output.lower() or "range" in result.output.lower()

    @patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock)
    def test_max_pages_above_ceiling_warns_and_clamps(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(
            cli,
            ["deps", "https://github.com/x/y", "--token", "ghp_x", "--max-pages", "5000"],
        )
        assert result.exit_code == 0
        assert "1000" in result.stderr  # warned about the cap
        _, kwargs = mock_run.call_args
        assert kwargs["max_pages"] == 1000


class TestSearchHardening:
    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_uses_bounded_non_adaptive_topk(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import CodeSearchResult

        mock_scrape.return_value = ScrapeResult(
            repos=[Repository(owner="a", name="b", url="https://github.com/a/b", stars=900)],
            pages_scraped=3,
            max_pages=200,
            estimated_total_pages=3,
            estimated_total_dependents=90,
            matched_count=1,
        )
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django", query="import os", hits=[], searched_repos=1
        )
        result = runner.invoke(
            cli,
            [
                "search",
                "https://github.com/django/django",
                "import os",
                "--token",
                "ghp_x",
                "--max-repos",
                "7",
            ],
        )
        assert result.exit_code == 0
        _, kwargs = mock_scrape.call_args
        assert kwargs["rows"] == 7  # bounded to --max-repos
        assert kwargs["adaptive_stop"] is False  # never heuristic on the search path

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_max_pages_above_ceiling_warns_and_clamps(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        """`search` mirrors `deps`: --max-pages above the ceiling warns and clamps."""
        from dep_rank.core.models import CodeSearchResult

        mock_scrape.return_value = ScrapeResult(
            repos=[],
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=1,
            estimated_total_dependents=0,
            matched_count=0,
        )
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django", query="import os", hits=[], searched_repos=0
        )
        result = runner.invoke(
            cli,
            [
                "search",
                "https://github.com/django/django",
                "import os",
                "--token",
                "ghp_x",
                "--max-pages",
                "5000",
            ],
        )
        assert result.exit_code == 0
        assert "1000" in result.stderr  # warned about the cap (mirrors deps)
        _, kwargs = mock_scrape.call_args
        assert kwargs["max_pages"] == 1000  # clamped before the scrape

    @patch("dep_rank.cli.app.appdirs.user_cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_summary_reports_matched_count_not_len_repos(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        """Once `search` bounds `repos` to top-K (`rows=max_repos`), the scrape
        summary must report `matched_count`, not `len(repos)`."""
        from dep_rank.core.models import CodeSearchResult

        mock_scrape.return_value = ScrapeResult(
            repos=[
                Repository(owner="a", name="b", url="https://github.com/a/b", stars=900),
                Repository(owner="c", name="d", url="https://github.com/c/d", stars=800),
            ],
            pages_scraped=200,
            max_pages=200,
            estimated_total_pages=200,
            estimated_total_dependents=15000,
            matched_count=4200,
        )
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django", query="import os", hits=[], searched_repos=2
        )
        result = runner.invoke(
            cli,
            [
                "search",
                "https://github.com/django/django",
                "import os",
                "--token",
                "ghp_x",
                "--max-repos",
                "2",
            ],
        )
        assert result.exit_code == 0
        assert "4,200" in result.output  # matched_count, not len(repos)==2
