"""Tests for CLI commands."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx2
import pytest
from click.testing import CliRunner

from dep_rank import __version__
from dep_rank.cli import app as cli_app
from dep_rank.cli.app import _cache_dir, _open_cache, cli
from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
    ScrapeReason,
    ScrapeResult,
    ScrapeSnapshot,
    TrustCheckResult,
    TrustMetadataResult,
)
from tests.conftest import (
    DEPENDENTS_HTML_LAST_PAGE,
    DEPENDENTS_HTML_NO_RESULTS,
    DEPENDENTS_HTML_PAGE_1,
    FakeHTTP,
    make_repo,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _scrape_result(
    repos: list[Repository],
    *,
    pages_scraped: int = 1,
    max_pages: int = 1000,
    estimated_total_pages: int = 30,
    estimated_total_dependents: int = 900,
    matched_count: int = 0,
    complete: bool = True,
    reason: ScrapeReason | None = None,
    stale_pages: int = 0,
) -> ScrapeResult:
    return ScrapeResult(
        repos=repos,
        pages_scraped=pages_scraped,
        max_pages=max_pages,
        estimated_total_pages=estimated_total_pages,
        estimated_total_dependents=estimated_total_dependents,
        matched_count=matched_count,
        complete=complete,
        reason=reason,
        stale_pages=stale_pages,
    )


@pytest.fixture
def mock_result() -> DependentsResult:
    return DependentsResult(
        source="https://github.com/django/django",
        total_count=3000,
        filtered_count=150,
        repos=[
            make_repo("alpha", "framework", stars=12500),
            make_repo("beta", "toolkit", stars=3200),
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
        assert _cache_dir is not cli_app._cache_dir

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
    async def test_closes_on_error(self) -> None:

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
            "print(sorted(m for m in ('httpx2', 'aiosqlite', 'selectolax') if m in sys.modules))"
        )
        out = subprocess.run(  # noqa: S603 - fixed test code in the current Python interpreter
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert out == "[]"


class TestLiveWarnings:
    def test_scraper_warning_survives_transient_cli_live(self) -> None:
        code = """import asyncio
import json
import logging
from tempfile import TemporaryDirectory
from unittest.mock import patch
from rich.live import Live
from dep_rank.cli.app import run_deps
from dep_rank.core.models import ScrapeResult

captured = []
class CapturingLive(Live):
    def start(self, *args, **kwargs):
        self.console._force_terminal = True
        self.capture = self.console.capture()
        self.capture.__enter__()
        super().start(*args, **kwargs)

    def stop(self):
        super().stop()
        self.capture.__exit__(None, None, None)
        captured.append(self.capture.get())

async def scrape(*args, **kwargs):
    logging.getLogger("dep_rank.core.scraper").warning("scraper warning survives")
    return ScrapeResult(repos=[], pages_scraped=1, max_pages=1,
                        estimated_total_pages=1, estimated_total_dependents=0)

with TemporaryDirectory() as cache_dir:
    with patch("dep_rank.cli.app._cache_dir", return_value=cache_dir), \
         patch("dep_rank.core.dependents.scrape_dependents", side_effect=scrape), \
         patch("rich.live.Live", CapturingLive):
        asyncio.run(run_deps("https://github.com/x/y", 2, 0, False, False, None))
print(json.dumps(captured))
"""
        result = subprocess.run(  # noqa: S603 - fixed test code in the current interpreter
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        captures = json.loads(result.stdout)
        assert len(captures) == 1
        assert "scraper warning survives" in captures[0]


class TestJsonStdout:
    def test_enrichment_warning_stays_off_json_stdout(self) -> None:
        code = """import tests.conftest
import json
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from tests.conftest import FakeHTTP
import functools
import httpx2
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
         patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock) as scrape:
        scrape.return_value = ScrapeResult(
            repos=repos, pages_scraped=1, max_pages=1000,
            estimated_total_pages=30, estimated_total_dependents=900,
        )
        fake = FakeHTTP()
        fake.post("https://api.github.com/graphql", payload=payload)
        with patch("httpx2.AsyncClient", functools.partial(
            httpx2.AsyncClient, transport=httpx2.MockTransport(fake)
        )):
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

    def test_scrape_summary_precedes_enrichment_warnings(self) -> None:
        code = """import tests.conftest
import json
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from tests.conftest import FakeHTTP
import functools
import httpx2
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
         patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock) as scrape:
        scrape.return_value = ScrapeResult(
            repos=repos, pages_scraped=1, max_pages=1000,
            estimated_total_pages=30, estimated_total_dependents=900,
        )
        fake = FakeHTTP()
        fake.post("https://api.github.com/graphql", payload=payload)
        with patch("httpx2.AsyncClient", functools.partial(
            httpx2.AsyncClient, transport=httpx2.MockTransport(fake)
        )):
            r = CliRunner().invoke(cli, ["deps", "https://github.com/x/y", "--descriptions",
                                       "--token", "t", "--rows", "2"])
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
        assert child["stderr"].index("Scraped 1/") < child["stderr"].index("partial errors")


class TestDepsCommand:
    def test_confirmed_zero_suppresses_table_and_trust_check(
        self,
        runner: CliRunner,
        mock_http: FakeHTTP,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_NO_RESULTS,
        )
        monkeypatch.setattr(cli_app, "_cache_dir", lambda: str(tmp_path))
        result = runner.invoke(
            cli,
            [
                "deps",
                "https://github.com/owner/repo",
                "--rank-by",
                "trust",
                "--trust-check",
                "--token",
                "ghp_x",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No dependents found." in result.output
        assert "Scraped" not in result.output
        assert "Top dependents of" not in result.output
        assert "Trust check (" not in result.output
        assert ("POST", "https://api.github.com/graphql") not in mock_http.requests

    def test_confirmed_zero_json_keeps_existing_schema(
        self,
        runner: CliRunner,
        mock_http: FakeHTTP,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_NO_RESULTS,
        )
        monkeypatch.setattr(cli_app, "_cache_dir", lambda: str(tmp_path))
        result = runner.invoke(cli, ["deps", "https://github.com/owner/repo", "--format", "json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["repos"] == []
        assert "confirmed_zero_dependents" not in payload
        assert "No dependents found." in result.stderr

    def test_missing_url(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["deps"])
        assert result.exit_code == 2
        assert "Missing argument 'URL'" in result.output

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


class TestDepsCommandFull:
    """Tests that exercise the full deps command body with mocked core functions."""

    @patch("dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_with_descriptions(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        mock_http: FakeHTTP,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = _scrape_result(repos)
        mock_enrich.return_value = TrustMetadataResult(
            repos=[
                repos[0].model_copy(update={"stars": 10, "description": "A"}),
                repos[1].model_copy(update={"stars": 900, "description": "B"}),
            ],
            failed=False,
            complete=True,
        )
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

    @patch("dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_descriptions_fetch_failure_falls_back(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        mock_http: FakeHTTP,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = _scrape_result(repos)
        mock_enrich.return_value = TrustMetadataResult(repos=repos, failed=True, complete=False)
        result = runner.invoke(
            cli, ["deps", mock_result.source, "--descriptions", "--token", "t", "--rows", "2"]
        )
        assert result.exit_code == 0
        assert result.stdout.index("alpha/framework") < result.stdout.index("beta/toolkit")
        assert "Trust metadata fetch failed" not in result.stderr

    @patch("dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_descriptions_rows_zero_skips_graphql(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        mock_http: FakeHTTP,
    ) -> None:
        repos = mock_result.repos
        mock_scrape.return_value = _scrape_result(repos)
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

    def test_search_invalid_url(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["search", "https://gitlab.com/foo/bar", "import os", "--token", "test-token"],
        )
        assert result.exit_code == 1
        assert "Error: URL must be a github.com repository URL, got: gitlab.com" in result.stderr


async def _seed_cli_cache(*bodies: bytes) -> None:
    cache = SqliteCache(cli_app._cache_dir())
    await cache.initialize()
    try:
        for index, body in enumerate(bodies):
            await cache.put(f"https://github.com/owner/repo/{index}", body, etag=None, ttl=3600)
    finally:
        await cache.close()


class TestCacheCommandsFull:
    def test_cache_clear(self, runner: CliRunner) -> None:
        asyncio.run(_seed_cli_cache(b"cached response"))
        result = runner.invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Cache cleared" in result.output
        stats = runner.invoke(cli, ["cache", "stats"])
        assert stats.exit_code == 0
        assert "Entries: 0" in stats.output

    def test_cache_stats(self, runner: CliRunner) -> None:
        asyncio.run(_seed_cli_cache(b"a" * 6000, b"b" * 6345))
        result = runner.invoke(cli, ["cache", "stats"])
        assert result.exit_code == 0
        assert "Entries: 2" in result.output
        assert "12,345" in result.output


class TestVersionOption:
    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestDepsHardeningFlags:
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_flags_pass_through_and_counts_use_matched(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
    ) -> None:

        mock_scrape.return_value = _scrape_result(
            [make_repo("a", "b", stars=900)],
            pages_scraped=200,
            max_pages=200,
            estimated_total_pages=500,
            estimated_total_dependents=15000,
            matched_count=4200,
            complete=False,
            reason=ScrapeReason.MAX_PAGES_REACHED,
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

        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert payload["reason"] == "max_pages_reached"
        assert payload["total_count"] == 4200
        assert "Scraped" not in result.stdout
        assert "Found" not in result.stdout
        assert "⚠" not in result.stdout

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_table_mode_shows_summary(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.return_value = _scrape_result(
            [make_repo("a", "b", stars=900)],
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

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_table_mode_prints_partial_warning(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.return_value = _scrape_result(
            [make_repo("a", "b", stars=900)],
            pages_scraped=200,
            max_pages=200,
            matched_count=1,
            complete=False,
            reason=ScrapeReason.MAX_PAGES_REACHED,
        )
        result = runner.invoke(
            cli, ["deps", "https://github.com/django/django", "--token", "ghp_x"]
        )
        assert result.exit_code == 0
        assert "Stopped at the page cap" in result.stderr

    @pytest.mark.parametrize(
        ("extra_args", "shown"),
        [
            pytest.param([], True, id="unauthenticated"),
            pytest.param(["--token", "ghp_x"], False, id="authenticated-refreshes-in-background"),
            pytest.param(["--format", "json"], True, id="json-stderr-only"),
        ],
    )
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_stale_cache_notice(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
        extra_args: list[str],
        shown: bool,
    ) -> None:
        mock_scrape.return_value = _scrape_result(
            [make_repo("a", "b", stars=900)], pages_scraped=10, matched_count=1, stale_pages=3
        )
        result = runner.invoke(cli, ["deps", "https://github.com/django/django", *extra_args])
        assert result.exit_code == 0
        stderr = " ".join(result.stderr.split())  # Rich wraps long lines
        assert ("3 of 10 pages came from expired cache entries" in stderr) is shown
        if shown:
            assert "dep-rank cache clear" in stderr
        if "json" in extra_args:
            json.loads(result.stdout)  # the notice never reaches stdout

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
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    def test_confirmed_zero_skips_search(
        self,
        mock_search: AsyncMock,
        runner: CliRunner,
        mock_http: FakeHTTP,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
            body=DEPENDENTS_HTML_NO_RESULTS,
        )
        monkeypatch.setattr(cli_app, "_cache_dir", lambda: str(tmp_path))
        result = runner.invoke(
            cli, ["search", "https://github.com/owner/repo", "needle", "--token", "ghp_x"]
        )
        assert result.exit_code == 0, result.output
        assert "No dependents found." in result.output
        assert "No results found" not in result.output
        assert "Scraped" not in result.output
        mock_search.assert_not_awaited()

    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_uses_bounded_non_adaptive_topk(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        runner: CliRunner,
    ) -> None:

        mock_scrape.return_value = _scrape_result(
            [make_repo("a", "b", stars=900)],
            pages_scraped=3,
            max_pages=200,
            estimated_total_pages=3,
            estimated_total_dependents=90,
            matched_count=1,
        )
        hit = CodeSearchHit(
            repo=make_repo("a", "b", stars=900),
            file_url="https://github.com/a/b/blob/main/setup.py",
            file_path="setup.py",
            matches=2,
        )
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[hit],
            searched_repos=1,
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
        assert "a/b" in result.output  # search results are printed

    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_max_pages_above_ceiling_warns_and_clamps(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        runner: CliRunner,
    ) -> None:
        """`search` mirrors `deps`: --max-pages above the ceiling warns and clamps."""

        mock_scrape.return_value = _scrape_result(
            [], estimated_total_pages=1, estimated_total_dependents=0
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

    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_summary_reports_matched_count_not_len_repos(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        runner: CliRunner,
    ) -> None:
        """Once `search` bounds `repos` to top-K (`rows=max_repos`), the scrape
        summary must report `matched_count`, not `len(repos)`."""

        mock_scrape.return_value = _scrape_result(
            [
                make_repo("a", "b", stars=900),
                make_repo("c", "d", stars=800),
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
    @patch("rich.live.Live.update")
    def test_live_path_drives_and_renders(
        self,
        mock_live_update: MagicMock,
        runner: CliRunner,
        mock_http: FakeHTTP,
    ) -> None:

        first = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
        page2 = "https://github.com/owner/repo/network/dependents?page=2"
        # Two pages so the top-K refines across more than one snapshot: page 1 has
        # alpha/beta/gamma and a Next link; page 2 adds delta/app and ends the walk.
        mock_http.get(first, body=DEPENDENTS_HTML_PAGE_1)
        mock_http.get(page2, body=DEPENDENTS_HTML_LAST_PAGE)
        with patch("httpx2.AsyncClient", side_effect=httpx2.AsyncClient) as ctor:
            result = runner.invoke(
                cli,
                ["deps", "https://github.com/owner/repo", "--token", "ghp_x", "--min-stars", "5"],
            )
        assert ctor.call_args.kwargs["trust_env"] is False
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
    return _scrape_result([], max_pages=200)


class TestOnPageCallbacks:
    @patch("rich.live.Live.update")
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_on_page_updates_live(
        self,
        mock_scrape: AsyncMock,
        mock_live_update: MagicMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.side_effect = _scrape_calling_on_page
        result = runner.invoke(cli, ["deps", "https://github.com/o/r", "--token", "ghp_x"])
        assert result.exit_code == 0, result.output
        assert mock_live_update.call_count == 1

    @patch("rich.progress.Progress.update")
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.scraper.scrape_dependents", new_callable=AsyncMock)
    def test_search_on_page_updates_progress(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_progress_update: MagicMock,
        runner: CliRunner,
    ) -> None:

        mock_scrape.side_effect = _scrape_calling_on_page
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/o/r", query="q", hits=[], searched_repos=0
        )
        result = runner.invoke(cli, ["search", "https://github.com/o/r", "q", "--token", "t"])
        assert result.exit_code == 0, result.output
        mock_progress_update.assert_any_call(
            ANY, completed=1, est_text="1/~30 estimated pages (3.33%)"
        )
        mock_progress_update.assert_any_call(ANY, total=1, completed=1)


class TestRankByTrust:
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
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_trust_pool_size_formula(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
        rows: int,
        expected_pool: int,
    ) -> None:
        # pool_size = 0 if rows <= 0 else max(rows, min(100, rows * 10))

        repos = [make_repo("a", "b", stars=10)]
        mock_scrape.return_value = _scrape_result(repos, matched_count=len(repos))
        with patch(
            "dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock
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

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_trust_json_includes_metadata(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
    ) -> None:

        repos = [
            make_repo("a", "b", stars=10),
            make_repo("c", "d", stars=20),
        ]
        mock_scrape.return_value = _scrape_result(repos, matched_count=len(repos))
        with patch(
            "dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock
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

    def test_star_json_unchanged_has_no_rank_metadata(self, runner: CliRunner) -> None:

        with patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = DependentsResult(
                source="https://github.com/django/django",
                total_count=1,
                filtered_count=1,
                repos=[make_repo("a", "b", stars=10)],
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

    @pytest.mark.parametrize(
        ("failed", "complete", "fmt"),
        [(True, False, "json"), (True, False, "table"), (False, False, "table")],
        ids=["failed-json", "failed-table", "partial-table"],
    )
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_trust_metadata_fallbacks(
        self,
        mock_scrape: AsyncMock,
        failed: bool,
        complete: bool,
        fmt: str,
        runner: CliRunner,
    ) -> None:
        repos = [make_repo("a", "b", stars=10)]
        mock_scrape.return_value = _scrape_result(repos, matched_count=len(repos))
        args = [
            "deps",
            "https://github.com/django/django",
            "--token",
            "ghp_x",
            "--rank-by",
            "trust",
        ]
        if fmt == "json":
            args += ["--format", "json"]
        with patch(
            "dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock
        ) as mock_trust:
            mock_trust.return_value = TrustMetadataResult(
                repos=repos, failed=failed, complete=complete
            )
            result = runner.invoke(cli, args)
        assert result.exit_code == 0
        if fmt == "json":
            payload = json.loads(result.stdout)
            assert payload["ranked_by"] == "stars"  # honest fallback
            assert payload["repos"][0]["trust"] is None
        elif failed:
            assert "falling back to star ranking" in result.output
        else:
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
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_trust_check_json_includes_only_requested_result(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
        trust_check: bool,
    ) -> None:

        repos = [
            make_repo("a", "b", stars=10),
            make_repo("c", "d", stars=20),
            make_repo("e", "f", stars=30),
        ]
        mock_scrape.return_value = _scrape_result(repos, matched_count=len(repos))

        async def check(
            session: httpx2.AsyncClient,
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
                "dep_rank.core.dependents.enrich_with_trust_metadata",
                new_callable=AsyncMock,
                return_value=TrustMetadataResult(repos=repos, failed=False, complete=True),
            ),
            patch("dep_rank.core.dependents.check_star_history", side_effect=check) as mock_check,
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
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_trust_check_unavailable_status_and_warning(
        self,
        mock_scrape: AsyncMock,
        runner: CliRunner,
        metadata_failed: bool,
        output_format: str,
    ) -> None:

        repos = [make_repo("a", "b", stars=10)]
        mock_scrape.return_value = _scrape_result(repos, matched_count=len(repos))

        async def check(
            session: httpx2.AsyncClient,
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
                "dep_rank.core.dependents.enrich_with_trust_metadata",
                new_callable=AsyncMock,
                return_value=TrustMetadataResult(
                    repos=repos, failed=metadata_failed, complete=not metadata_failed
                ),
            ),
            patch("dep_rank.core.dependents.check_star_history", side_effect=check) as mock_check,
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
