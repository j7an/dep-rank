"""Tests for CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses
from click.testing import CliRunner

from dep_rank import __version__
from dep_rank.cli.app import cli
from dep_rank.core.models import (
    DependentsResult,
    DependentType,
    Repository,
    ScrapeResult,
    ScrapeSnapshot,
    TrustMetadataResult,
)


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


class TestCacheDir:
    @pytest.mark.parametrize(
        ("platform", "case"),
        [
            ("darwin", "darwin"),
            ("linux", "xdg-set"),
            ("linux", "xdg-empty"),
            ("linux", "xdg-unset"),
            ("win32", "win-shell"),
        ],
        ids=["darwin", "xdg-set", "xdg-empty", "xdg-unset", "win-shell"],
    )
    def test_paths(
        self, platform: str, case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from dep_rank.cli.app import _cache_dir

        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        home = os.path.expanduser("~")
        if case == "darwin":
            expected = os.path.join(home, "Library", "Caches", "dep-rank")
        elif case == "xdg-set":
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
            expected = os.path.join(str(tmp_path), "dep-rank")
        elif case == "win-shell":
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "env"))
            monkeypatch.setattr(
                "dep_rank.cli.app._win_local_appdata", lambda: str(tmp_path / "shell")
            )
            expected = os.path.join(str(tmp_path / "shell"), "dep-rank", "dep-rank", "Cache")
        else:
            if case == "xdg-empty":
                monkeypatch.setenv("XDG_CACHE_HOME", "")
            expected = os.path.join(home, ".cache", "dep-rank")
        assert _cache_dir() == expected


class TestOpenCache:
    async def test_closes_on_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from dep_rank.cli.app import _open_cache

        monkeypatch.setattr("dep_rank.cli.app._cache_dir", lambda: str(tmp_path))
        with pytest.raises(RuntimeError, match="boom"):
            async with _open_cache() as cache:
                held = cache
                raise RuntimeError("boom")
        with pytest.raises(RuntimeError, match="not initialized"):
            await held.get("x")


class TestImportCost:
    def test_cli_import_does_not_load_network_stack(self) -> None:
        code = (
            "import sys, dep_rank.cli.app; "
            "print(sorted(m for m in ('aiohttp', 'aiosqlite', 'selectolax') if m in sys.modules))"
        )
        out = subprocess.run(  # noqa: S603 - fixed test code in the current Python interpreter
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert out == "[]"


class TestJsonStdout:
    def test_enrichment_warning_stays_off_json_stdout(self) -> None:
        code = """import tests.conftest
import json
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from aioresponses import aioresponses
from click.testing import CliRunner
from dep_rank.cli.app import cli
from dep_rank.core.models import Repository, ScrapeResult

repos = [
    Repository(owner="alpha", name="framework",
               url="https://github.com/alpha/framework", stars=12500),
    Repository(owner="beta", name="toolkit", url="https://github.com/beta/toolkit", stars=3200),
]
payload = {
    "data": {"repo_0": {"stargazerCount": 10, "description": "A"}, "repo_1": None},
    "errors": [{"type": "NOT_FOUND", "message": "x"}],
}
with TemporaryDirectory() as cache_dir:
    with patch("dep_rank.cli.app._cache_dir", return_value=cache_dir), \
         patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock) as scrape:
        scrape.return_value = ScrapeResult(
            repos=repos, pages_scraped=1, max_pages=1000,
            estimated_total_pages=30, estimated_total_dependents=900,
        )
        with aioresponses() as responses:
            responses.post("https://api.github.com/graphql", payload=payload)
            r = CliRunner().invoke(cli, ["deps", "https://github.com/x/y", "--descriptions",
                                       "--token", "t", "--format", "json", "--rows", "2"])
        print(json.dumps({"stdout": r.stdout, "stderr": r.stderr, "code": r.exit_code}))
"""
        result = subprocess.run(  # noqa: S603 - fixed test code in the current interpreter
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )
        child = json.loads(result.stdout)
        assert child["code"] == 0
        assert json.loads(child["stdout"])["repos"]
        assert "partial errors" in child["stderr"]


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
        assert result.exit_code == 1
        assert "Error: URL must be a github.com repository URL, got: gitlab.com" in result.stderr


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

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_with_descriptions(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = ScrapeResult(
            repos=repos,
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        mock_enrich.return_value = TrustMetadataResult(
            repos=[
                repos[0].model_copy(update={"stars": 10, "description": "A"}),
                repos[1].model_copy(update={"stars": 900, "description": "B"}),
            ],
            failed=False,
            complete=True,
        )
        with aioresponses():
            result = runner.invoke(
                cli,
                [
                    "deps",
                    mock_result.source,
                    "--descriptions",
                    "--token",
                    "test-token",
                    "--rows",
                    "2",
                    "--format",
                    "json",
                ],
            )
        assert result.exit_code == 0
        assert mock_enrich.call_args.kwargs["include_description"] is True
        assert result.stdout.index('"toolkit"') < result.stdout.index('"framework"')

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_descriptions_fetch_failure_falls_back(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = ScrapeResult(
            repos=repos,
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        mock_enrich.return_value = TrustMetadataResult(repos=repos, failed=True, complete=False)
        with aioresponses():
            result = runner.invoke(
                cli, ["deps", mock_result.source, "--descriptions", "--token", "t", "--rows", "2"]
            )
        assert result.exit_code == 0
        assert result.stdout.index("alpha/framework") < result.stdout.index("beta/toolkit")
        assert "Trust metadata fetch failed" not in result.stderr

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_descriptions_rows_zero_skips_graphql(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = ScrapeResult(
            repos=repos,
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
        )
        with aioresponses():
            result = runner.invoke(
                cli, ["deps", mock_result.source, "--descriptions", "--token", "t", "--rows", "0"]
            )
        assert result.exit_code == 0
        mock_enrich.assert_not_called()

    def test_deps_descriptions_without_token(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps", "https://github.com/django/django", "--descriptions"])
        assert result.exit_code != 0
        assert "requires a GitHub token" in result.output


class TestSearchCommandFull:
    """Tests that exercise the full search command body."""

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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
        assert result.exit_code == 1
        assert "Error: URL must be a github.com repository URL, got: gitlab.com" in result.stderr


class TestCacheCommandsFull:
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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
        assert "concurrency" not in kwargs
        assert "deprecated" in result.stderr
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

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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

    @patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock)
    def test_concurrency_is_deprecated_noop(
        self, mock_run: AsyncMock, runner: CliRunner, mock_result: DependentsResult
    ) -> None:
        mock_run.return_value = mock_result
        result = runner.invoke(
            cli, ["deps", "https://github.com/x/y", "--token", "ghp_x", "--concurrency", "11"]
        )
        assert result.exit_code == 0
        assert "has no effect; dependents pages are fetched serially" in result.stderr
        assert "concurrency" not in mock_run.call_args.kwargs
        assert "--concurrency" not in runner.invoke(cli, ["deps", "--help"]).output

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
        assert "Warning: --max-pages capped at the 1000 ceiling." in result.stderr
        _, kwargs = mock_run.call_args
        assert kwargs["max_pages"] == 1000


class TestSearchHardening:
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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
                "--concurrency",
                "0",
            ],
        )
        assert result.exit_code == 0
        assert "has no effect; dependents pages are fetched serially" in result.stderr
        assert "--concurrency" not in runner.invoke(cli, ["search", "--help"]).output
        _, kwargs = mock_scrape.call_args
        assert "concurrency" not in kwargs
        assert kwargs["rows"] == 7  # bounded to --max-repos
        assert kwargs["adaptive_stop"] is False  # never heuristic on the search path

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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
        assert "Warning: --max-pages capped at the 1000 ceiling." in result.stderr
        _, kwargs = mock_scrape.call_args
        assert kwargs["max_pages"] == 1000  # clamped before the scrape

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
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


class TestDepsLiveTopK:
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.get", new_callable=AsyncMock, return_value=None)
    @patch("dep_rank.core.cache.SqliteCache.put", new_callable=AsyncMock)
    @patch("rich.live.Live.update")
    def test_live_path_drives_and_renders(
        self,
        mock_live_update: MagicMock,
        mock_put: AsyncMock,
        mock_get: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from aioresponses import aioresponses

        from tests.conftest import DEPENDENTS_HTML_LAST_PAGE, DEPENDENTS_HTML_PAGE_1

        first = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
        page2 = "https://github.com/owner/repo/network/dependents?page=2"
        with aioresponses() as m:
            # Two pages so the top-K refines across more than one snapshot: page 1 has
            # alpha/beta/gamma and a Next link; page 2 adds delta/app and ends the walk.
            m.get(first, body=DEPENDENTS_HTML_PAGE_1)
            m.get(page2, body=DEPENDENTS_HTML_LAST_PAGE)
            result = runner.invoke(
                cli,
                ["deps", "https://github.com/owner/repo", "--token", "ghp_x", "--min-stars", "5"],
            )
        assert result.exit_code == 0
        # Repos from BOTH pages appear in the final (post-Live) summary table -> the walk
        # consumed both pages.
        assert "alpha/framework" in result.output
        assert "delta/app" in result.output
        # Live.update fired at least once per page snapshot (>=2).
        assert mock_live_update.call_count >= 2


async def _scrape_calling_on_page(*args: object, on_page: Any, **kwargs: object) -> ScrapeResult:
    await on_page(
        ScrapeSnapshot(
            top_k=[],
            pages_scraped=1,
            estimated_total_pages=30,
            estimated_total_dependents=900,
            matched_count=0,
        )
    )
    return ScrapeResult(
        repos=[],
        pages_scraped=1,
        max_pages=200,
        estimated_total_pages=30,
        estimated_total_dependents=900,
    )


class TestOnPageCallbacks:
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("rich.live.Live.update")
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_deps_on_page_updates_live(
        self,
        mock_scrape: AsyncMock,
        mock_live_update: MagicMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.side_effect = _scrape_calling_on_page
        result = runner.invoke(cli, ["deps", "https://github.com/o/r", "--token", "ghp_x"])
        assert result.exit_code == 0, result.output
        assert mock_live_update.call_count == 1

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("rich.progress.Progress.update")
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_on_page_updates_progress(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_progress_update: MagicMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import CodeSearchResult

        mock_scrape.side_effect = _scrape_calling_on_page
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/o/r", query="q", hits=[], searched_repos=0
        )
        result = runner.invoke(cli, ["search", "https://github.com/o/r", "q", "--token", "t"])
        assert result.exit_code == 0, result.output
        mock_progress_update.assert_called_once_with(
            ANY, completed=1, est_text="1/~30 estimated pages (3.33%)"
        )


class TestRankByTrust:
    def _scrape_result(self, repos: list[Repository]) -> ScrapeResult:
        return ScrapeResult(
            repos=repos,
            pages_scraped=1,
            max_pages=1000,
            estimated_total_pages=30,
            estimated_total_dependents=900,
            matched_count=len(repos),
        )

    def test_trust_without_token_errors(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli, ["deps", "https://github.com/django/django", "--rank-by", "trust"]
        )
        assert result.exit_code != 0
        assert "requires a GitHub token" in result.output

    @pytest.mark.parametrize(
        ("rows", "expected_pool"),
        [(0, 0), (5, 50), (10, 100), (50, 100), (200, 200)],
    )
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_trust_pool_size_formula(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        rows: int,
        expected_pool: int,
    ) -> None:
        # pool_size = 0 if rows <= 0 else max(rows, min(100, rows * 10))
        from dep_rank.core.models import TrustMetadataResult

        repos = [Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)]
        mock_scrape.return_value = self._scrape_result(repos)
        with patch(
            "dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(repos=repos, failed=False, complete=True)
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/django/django",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                    "--rows",
                    str(rows),
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_scrape.call_args
        assert kwargs["rows"] == expected_pool

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_trust_json_includes_metadata(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        import json

        from dep_rank.core.models import TrustMetadataResult

        repos = [
            Repository(owner="a", name="b", url="https://github.com/a/b", stars=10),
            Repository(owner="c", name="d", url="https://github.com/c/d", stars=20),
        ]
        mock_scrape.return_value = self._scrape_result(repos)
        with patch(
            "dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(repos=repos, failed=False, complete=True)
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/django/django",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                    "--format",
                    "json",
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ranked_by"] == "trust"
        assert payload["repos"][0]["trust"] is not None
        assert "score" in payload["repos"][0]["trust"]
        assert payload["repos"][0]["trust"]["cautions"] == []  # zero or more, always a list

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_trust_fetch_failure_falls_back_to_stars(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        import json

        from dep_rank.core.models import TrustMetadataResult

        repos = [Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)]
        mock_scrape.return_value = self._scrape_result(repos)
        with patch(
            "dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(repos=repos, failed=True, complete=False)
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/django/django",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                    "--format",
                    "json",
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ranked_by"] == "stars"  # honest fallback
        assert payload["repos"][0]["trust"] is None

    def test_star_json_unchanged_has_no_rank_metadata(self, runner: CliRunner) -> None:
        import json

        with patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = DependentsResult(
                source="https://github.com/django/django",
                total_count=1,
                filtered_count=1,
                repos=[Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)],
                dependent_type=DependentType.REPOSITORY,
                scraped_at=datetime.now(tz=UTC),
            )
            result = runner.invoke(
                cli, ["deps", "https://github.com/django/django", "--format", "json"]
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "ranked_by" not in payload
        assert "trust" not in result.stdout

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_fallback_emits_warning_in_table_mode(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import TrustMetadataResult

        repos = [Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)]
        mock_scrape.return_value = self._scrape_result(repos)
        with patch(
            "dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(repos=repos, failed=True, complete=False)
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/django/django",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                ],  # table mode (no --format json)
            )
        assert result.exit_code == 0
        assert "falling back to star ranking" in result.output

    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_partial_metadata_emits_warning_in_table_mode(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
    ) -> None:
        from dep_rank.core.models import TrustMetadataResult

        repos = [Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)]
        mock_scrape.return_value = self._scrape_result(repos)
        with patch(
            "dep_rank.core.graphql.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(repos=repos, failed=False, complete=False)
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/django/django",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                ],  # table mode
            )
        assert result.exit_code == 0
        assert "partial data" in result.output

    def test_trust_check_requires_rank_by_trust(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps", "https://github.com/a/b", "--trust-check"])
        assert result.exit_code != 0
        assert "Error: --trust-check requires --rank-by trust" in result.stderr

    def test_trust_check_without_token_errors(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli, ["deps", "https://github.com/a/b", "--rank-by", "trust", "--trust-check"]
        )
        assert result.exit_code != 0
        assert "requires a GitHub token" in result.stderr

    @pytest.mark.parametrize("trust_check", [False, True])
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_trust_check_json_includes_only_requested_result(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        trust_check: bool,
    ) -> None:
        import json

        from dep_rank.core.models import TrustCheckResult, TrustMetadataResult

        repos = [
            Repository(owner="a", name="b", url="https://github.com/a/b", stars=10),
            Repository(owner="c", name="d", url="https://github.com/c/d", stars=20),
            Repository(owner="e", name="f", url="https://github.com/e/f", stars=30),
        ]
        mock_scrape.return_value = self._scrape_result(repos)

        async def check(
            session: aiohttp.ClientSession,
            selected: list[Repository],
            token: str,
            *,
            now: datetime,
        ) -> tuple[list[Repository], TrustCheckResult]:
            # The check must see scored, final selected results, using the output clock.
            assert len(selected) == 2
            assert all(repo.trust is not None for repo in selected)
            return selected, TrustCheckResult(complete=True, window_weeks=30, repos_checked=2)

        with (
            patch(
                "dep_rank.core.graphql.enrich_with_trust_metadata",
                new_callable=AsyncMock,
                return_value=TrustMetadataResult(repos=repos, failed=False, complete=True),
            ),
            patch("dep_rank.core.star_history.check_star_history", side_effect=check) as mock_check,
        ):
            args = [
                "deps",
                "https://github.com/a/b",
                "--token",
                "ghp_x",
                "--rank-by",
                "trust",
                "--rows",
                "2",
                "--format",
                "json",
            ]
            if trust_check:
                args.append("--trust-check")
            result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert result.stderr == ""
        if trust_check:
            assert payload["trust_check"]["repos_checked"] == 2
            mock_check.assert_awaited_once()
            checked_repos = mock_check.call_args.args[1]
            assert [repo.url for repo in checked_repos] == [
                repo["url"] for repo in payload["repos"]
            ]
            assert mock_check.call_args.kwargs["now"] == datetime.fromisoformat(
                payload["scraped_at"]
            )
        else:
            mock_check.assert_not_called()
            assert "trust_check" not in payload

    @pytest.mark.parametrize("output_format", ["table", "json"])
    @pytest.mark.parametrize("metadata_failed", [False, True])
    @patch("dep_rank.cli.app._cache_dir", return_value="/tmp/test-cache")  # noqa: S108
    @patch("dep_rank.core.cache.SqliteCache.close", new_callable=AsyncMock)
    @patch("dep_rank.core.cache.SqliteCache.initialize", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_trust_check_unavailable_status_and_warning(
        self,
        mock_scrape: AsyncMock,
        mock_init: AsyncMock,
        mock_close: AsyncMock,
        mock_cache_dir: AsyncMock,
        runner: CliRunner,
        metadata_failed: bool,
        output_format: str,
    ) -> None:
        import json

        from dep_rank.core.models import TrustCheckResult, TrustMetadataResult

        repos = [Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)]
        mock_scrape.return_value = self._scrape_result(repos)

        async def check(
            session: aiohttp.ClientSession,
            selected: list[Repository],
            token: str,
            *,
            now: datetime,
        ) -> tuple[list[Repository], TrustCheckResult]:
            return selected, TrustCheckResult(
                complete=False, window_weeks=30, repos_checked=1, unavailable=["a/b"]
            )

        with (
            patch(
                "dep_rank.core.graphql.enrich_with_trust_metadata",
                new_callable=AsyncMock,
                return_value=TrustMetadataResult(
                    repos=repos, failed=metadata_failed, complete=not metadata_failed
                ),
            ),
            patch("dep_rank.core.star_history.check_star_history", side_effect=check) as mock_check,
        ):
            result = runner.invoke(
                cli,
                [
                    "deps",
                    "https://github.com/a/b",
                    "--token",
                    "ghp_x",
                    "--rank-by",
                    "trust",
                    "--trust-check",
                    "--format",
                    output_format,
                ],
            )
        assert result.exit_code == 0, result.output
        if metadata_failed:
            mock_check.assert_not_called()
        else:
            mock_check.assert_awaited_once()
        if output_format == "json":
            assert result.stderr == ""
            payload = json.loads(result.stdout)
            assert payload["trust_check"] == {
                "complete": False,
                "window_weeks": 30,
                "repos_checked": 1,
                "insufficient_history": [],
                "unavailable": ["a/b"],
            }
            assert payload["ranked_by"] == ("stars" if metadata_failed else "trust")
        elif metadata_failed:
            assert "Trust check skipped — trust ranking unavailable." in result.stderr
        else:
            assert "Trust check incomplete" in result.stderr
            assert "1 repos" in result.stderr
