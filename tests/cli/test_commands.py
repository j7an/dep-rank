"""Tests for CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

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


class TestMcpCommand:
    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "transport" in result.output


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


class TestMcpCommandFull:
    def test_mcp_import_error(self, runner: CliRunner) -> None:
        """Test that mcp command handles ImportError gracefully."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if name == "dep_rank.mcp.server":
                raise ImportError("no fastmcp")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = runner.invoke(cli, ["mcp"])
        assert result.exit_code != 0
        assert "fastmcp" in result.output or "MCP" in result.output


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
        assert "0.1.0" in result.output
