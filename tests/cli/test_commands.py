"""Tests for CLI commands."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx2
import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from dep_rank import __version__
from dep_rank.cli import app as cli_app
from dep_rank.cli.app import cli
from dep_rank.cli.formatters import RetryCountdown
from dep_rank.core.cache import open_cache
from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    DownloadsCheckResult,
    Repository,
    RetryStatus,
    ScrapeReason,
    ScrapeResult,
    ScrapeSnapshot,
    TrustCheckResult,
    TrustMetadataResult,
)
from tests.conftest import (
    DEPENDENTS_HTML_EMPTY,
    DEPENDENTS_HTML_LAST_PAGE,
    DEPENDENTS_HTML_NO_HEADER,
    DEPENDENTS_HTML_PAGE_1,
    FakeHTTP,
    dependents_page,
    make_repo,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _scrape_result(repos: list[Repository], **fields: Any) -> ScrapeResult:
    base = {
        "pages_scraped": 1,
        "max_pages": 1000,
        "estimated_total_pages": 30,
        "estimated_total_dependents": 900,
    }
    return ScrapeResult(repos=repos, **{**base, **fields})


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


class TestImportCost:
    def test_cli_import_does_not_load_network_stack(self) -> None:
        code = (
            "import sys, dep_rank.cli.app; "
            "print(sorted(m for m in ('httpx2', 'sqlite3', 'selectolax') if m in sys.modules))"
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
    with patch("dep_rank.core.cache.default_cache_dir", return_value=cache_dir), \
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


_DESCRIPTIONS_CHILD = """import tests.conftest
import json
import sys
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
    with patch("dep_rank.core.cache.default_cache_dir", return_value=cache_dir), \
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
                                       "--token", "t", "--rows", "2", *sys.argv[1:]])
        print(json.dumps({"stdout": r.stdout, "stderr": r.stderr, "code": r.exit_code}))
"""


def _descriptions_child(*extra_args: str) -> dict[str, Any]:
    """Run ``deps --descriptions`` against a partial GraphQL response in a fresh process."""
    result = subprocess.run(  # noqa: S603 - fixed test code in the current interpreter
        [sys.executable, "-c", _DESCRIPTIONS_CHILD, *extra_args],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    child: dict[str, Any] = json.loads(result.stdout)
    return child


class TestJsonStdout:
    def test_enrichment_warning_stays_off_json_stdout(self) -> None:
        child = _descriptions_child("--format", "json")
        assert child["code"] == 0
        assert json.loads(child["stdout"])["repos"]
        assert "partial errors" in child["stderr"]

    def test_scrape_summary_precedes_enrichment_warnings(self) -> None:
        child = _descriptions_child()
        assert child["code"] == 0
        assert child["stderr"].index("Scraped 1 page") < child["stderr"].index("partial errors")


@pytest.mark.parametrize(
    ("args", "exit_code", "message"),
    [
        pytest.param(["deps"], 2, "Missing argument 'URL'", id="deps-missing-url"),
        pytest.param(
            ["deps", "https://gitlab.com/foo/bar"],
            1,
            "Error: URL must be a github.com repository URL, got: gitlab.com",
            id="deps-invalid-url",
        ),
        pytest.param(
            ["search", "https://github.com/django/django", "import os"],
            2,
            "Missing option '--token'",
            id="search-missing-token",
        ),
        pytest.param(
            ["search", "https://gitlab.com/foo/bar", "import os", "--token", "test-token"],
            1,
            "Error: URL must be a github.com repository URL, got: gitlab.com",
            id="search-invalid-url",
        ),
        pytest.param(
            ["deps", "https://github.com/django/django", "--descriptions"],
            1,
            "requires a GitHub token",
            id="descriptions-without-token",
        ),
        pytest.param(
            ["deps", "https://github.com/django/django", "--rank-by", "trust"],
            1,
            "requires a GitHub token",
            id="trust-without-token",
        ),
        pytest.param(
            ["deps", "https://github.com/a/b", "--trust-check"],
            1,
            "Error: --trust-check requires --rank-by trust",
            id="trust-check-without-rank-by-trust",
        ),
        pytest.param(
            ["deps", "https://github.com/a/b", "--rank-by", "trust", "--trust-check"],
            1,
            "requires a GitHub token",
            id="trust-check-without-token",
        ),
    ],
)
def test_argument_errors(runner: CliRunner, args: list[str], exit_code: int, message: str) -> None:
    result = runner.invoke(cli, args)
    assert result.exit_code == exit_code
    assert message in result.stderr


class TestDepsCommand:
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

    @pytest.mark.parametrize("downloads", [False, True])
    @patch("dep_rank.cli.app.run_deps", new_callable=AsyncMock)
    def test_downloads_flag_reaches_run_deps(
        self,
        mock_run: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        downloads: bool,
    ) -> None:
        mock_run.return_value = mock_result
        args = ["deps", mock_result.source]
        if downloads:
            args.append("--downloads")
        result = runner.invoke(cli, args)
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["downloads"] is downloads

    @pytest.mark.parametrize("mode", ["--packages", "--repositories"])
    @pytest.mark.parametrize("output_format", ["table", "json"])
    @patch("dep_rank.core.dependents.fetch_downloads", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_incomplete_downloads_warns_on_stderr_and_json_stays_parseable(
        self,
        mock_scrape: AsyncMock,
        mock_downloads: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        mock_http: FakeHTTP,
        mode: str,
        output_format: str,
    ) -> None:
        mock_scrape.return_value = _scrape_result(mock_result.repos)
        mock_downloads.return_value = (
            mock_result.repos,
            DownloadsCheckResult(complete=False, unavailable=["alpha/framework", "beta/toolkit"]),
        )
        result = runner.invoke(
            cli, ["deps", mock_result.source, "--downloads", mode, "--format", output_format]
        )
        assert result.exit_code == 0
        assert result.stderr.count("Download lookup incomplete — unavailable for 2 repos.") == 1
        assert "Download lookup incomplete" not in result.stdout
        assert mock_scrape.call_args.kwargs["token"] is None
        assert mock_scrape.call_args.kwargs["dependent_type"] == (
            DependentType.PACKAGE if mode == "--packages" else DependentType.REPOSITORY
        )
        if output_format == "json":
            payload = json.loads(result.stdout)
            assert payload["downloads_check"] == {
                "complete": False,
                "unavailable": ["alpha/framework", "beta/toolkit"],
            }
            assert all(repo["downloads"] is None for repo in payload["repos"])
        else:
            assert "Downloads" in result.stdout
            assert result.stdout.count("unavailable") == 2

    @pytest.mark.parametrize("output_format", ["table", "json"])
    @patch("dep_rank.core.dependents.fetch_downloads", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_complete_downloads_does_not_warn(
        self,
        mock_scrape: AsyncMock,
        mock_downloads: AsyncMock,
        runner: CliRunner,
        mock_result: DependentsResult,
        mock_http: FakeHTTP,
        output_format: str,
    ) -> None:
        mock_scrape.return_value = _scrape_result(mock_result.repos)
        mock_downloads.return_value = (mock_result.repos, DownloadsCheckResult(complete=True))
        result = runner.invoke(
            cli, ["deps", mock_result.source, "--downloads", "--format", output_format]
        )
        assert result.exit_code == 0
        assert "Download lookup incomplete" not in result.stderr
        assert "Download lookup incomplete" not in result.stdout
        if output_format == "json":
            assert json.loads(result.stdout)["downloads_check"] == {
                "complete": True,
                "unavailable": [],
            }
        else:
            assert "Downloads" in result.stdout


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


async def _seed_cli_cache(*bodies: bytes) -> None:
    async with open_cache() as cache:
        for index, body in enumerate(bodies):
            await cache.put(f"https://github.com/owner/repo/{index}", body, etag=None, ttl=3600)


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
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
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
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
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
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
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


def _frame_height(frame: Any) -> int:
    return len(Console(width=80).render_lines(frame))


async def _scrape_tall_table(*args: object, on_page: Any, **kwargs: object) -> ScrapeResult:
    repos = [make_repo("o", f"r{i}", stars=1000 - i) for i in range(50)]
    await on_page(
        ScrapeSnapshot(
            top_k=repos,
            pages_scraped=1,
            estimated_total_pages=1,
            estimated_total_dependents=50,
            matched_count=50,
        )
    )
    return _scrape_result(repos, matched_count=50)


class TestDepsLiveFrames:
    """Live.stop() re-renders its frame uncropped, so that frame must fit the terminal."""

    @pytest.fixture
    def events(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
        log: list[tuple[str, Any]] = []
        update, stop = Live.update, Live.stop

        def spy_update(self: Live, renderable: Any, *, refresh: bool = False) -> None:
            log.append(("update", renderable))
            update(self, renderable, refresh=refresh)

        def spy_stop(self: Live) -> None:
            log.append(("stop", self.renderable))
            stop(self)

        monkeypatch.setattr(Live, "update", spy_update)
        monkeypatch.setattr(Live, "stop", spy_stop)
        return log

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_tall_table_is_cleared_before_stop(
        self, mock_scrape: AsyncMock, events: list[tuple[str, Any]]
    ) -> None:
        mock_scrape.side_effect = _scrape_tall_table
        asyncio.run(cli_app.run_deps("https://github.com/o/r", 50, 0, False, False, "t"))
        stops = [frame for kind, frame in events if kind == "stop"]
        assert stops
        assert all(_frame_height(frame) <= 1 for frame in stops)
        # Nothing to wait for after a plain star-ranked scrape, so no spinner.
        assert not any(isinstance(frame, Spinner) for _, frame in events)

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_tall_table_is_cleared_when_scrape_fails(
        self, mock_scrape: AsyncMock, events: list[tuple[str, Any]]
    ) -> None:
        async def page_then_fail(*args: object, on_page: Any, **kwargs: object) -> ScrapeResult:
            await _scrape_tall_table(on_page=on_page)
            raise RuntimeError("boom")

        mock_scrape.side_effect = page_then_fail
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(cli_app.run_deps("https://github.com/o/r", 50, 0, False, False, "t"))
        stops = [frame for kind, frame in events if kind == "stop"]
        assert stops
        assert all(_frame_height(frame) <= 1 for frame in stops)

    @pytest.mark.parametrize(
        ("rank_by", "trust_check", "descriptions", "downloads", "rows", "expected"),
        [
            (
                "trust",
                False,
                False,
                False,
                5,
                "Trust-scoring the 50 most-starred dependents to show the top 5…",
            ),
            (
                "trust",
                True,
                False,
                False,
                5,
                "Trust-scoring the 50 most-starred dependents to show the top 5"
                " and checking star history…",
            ),
            # Fewer scraped than --rows: every scored repo is shown, so no "top N" promise.
            ("trust", False, False, False, 100, "Trust-scoring 50 dependents…"),
            ("stars", False, True, False, 5, "Fetching descriptions…"),
            (
                "trust",
                False,
                False,
                True,
                5,
                "Trust-scoring the 50 most-starred dependents to show the top 5"
                " and looking up downloads…",
            ),
            (
                "trust",
                True,
                False,
                True,
                5,
                "Trust-scoring the 50 most-starred dependents to show the top 5"
                " and checking star history and looking up downloads…",
            ),
            ("stars", False, False, True, 5, "Looking up package downloads…"),
            (
                "stars",
                False,
                True,
                True,
                5,
                "Fetching descriptions and looking up package downloads…",
            ),
        ],
    )
    @patch("dep_rank.core.dependents.fetch_downloads", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.check_star_history", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.enrich_with_trust_metadata", new_callable=AsyncMock)
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_spinner_shows_while_enriching(
        self,
        mock_scrape: AsyncMock,
        mock_enrich: AsyncMock,
        mock_check: AsyncMock,
        mock_downloads: AsyncMock,
        events: list[tuple[str, Any]],
        rank_by: str,
        trust_check: bool,
        descriptions: bool,
        downloads: bool,
        rows: int,
        expected: str,
    ) -> None:
        mock_scrape.side_effect = _scrape_tall_table

        async def enrich(session: object, repos: list[Repository], *a: object, **k: object) -> Any:
            events.append(("enrich", None))
            return TrustMetadataResult(repos=repos, failed=False, complete=True)

        async def lookup(session: object, repos: list[Repository]) -> Any:
            events.append(("enrich", None))
            return repos, DownloadsCheckResult(complete=True)

        mock_downloads.side_effect = lookup
        mock_enrich.side_effect = enrich
        mock_check.side_effect = lambda session, repos, *a, **k: (
            repos,
            TrustCheckResult(complete=True, window_weeks=30, repos_checked=len(repos)),
        )
        asyncio.run(
            cli_app.run_deps(
                "https://github.com/o/r",
                rows,
                0,
                descriptions,
                False,
                "t",
                rank_by=rank_by,
                trust_check=trust_check,
                downloads=downloads,
            )
        )
        kinds = [kind for kind, _ in events]
        shown = [frame for kind, frame in events[: kinds.index("enrich")] if kind == "update"]
        assert isinstance(shown[-1], Spinner)
        assert str(shown[-1].text) == expected
        assert all(_frame_height(frame) <= 1 for kind, frame in events if kind == "stop")


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


RETRY = RetryStatus(page=1, attempt=1, max_retries=5, delay=120)


async def _scrape_retrying_then_page(
    *args: object, on_retry: Any, on_page: Any, **kwargs: object
) -> ScrapeResult:
    await on_retry(RETRY)
    return await _scrape_calling_on_page(on_page=on_page)


class TestRetryStatus:
    @pytest.fixture(autouse=True)
    def _terminal_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The countdown is only wired up when stderr can render live updates.
        monkeypatch.setattr(cli_app._stderr_console, "_force_terminal", True)
        monkeypatch.delenv("TERM", raising=False)  # "dumb" would disable live rendering

    @patch("rich.live.Live.update")
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_shows_countdown_in_live_then_clears(
        self, mock_scrape: AsyncMock, mock_live_update: MagicMock, runner: CliRunner
    ) -> None:
        mock_scrape.side_effect = _scrape_retrying_then_page
        result = runner.invoke(cli, ["deps", "https://github.com/o/r", "--token", "ghp_x"])
        assert result.exit_code == 0, result.output
        shown = [c.args[0] for c in mock_live_update.call_args_list]
        assert isinstance(shown[0], RetryCountdown)  # before page 1: countdown alone
        assert not isinstance(shown[1], RetryCountdown)  # the next page clears it

    @patch("rich.live.Live.update")
    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_countdown_sits_above_existing_table(
        self, mock_scrape: AsyncMock, mock_live_update: MagicMock, runner: CliRunner
    ) -> None:
        """Live crops overflow from the bottom, so the countdown must lead a tall table."""

        async def page_then_retry(*args: object, on_retry: Any, on_page: Any, **_: object) -> Any:
            result = await _scrape_calling_on_page(on_page=on_page)
            await on_retry(RETRY)
            return result

        mock_scrape.side_effect = page_then_retry
        result = runner.invoke(cli, ["deps", "https://github.com/o/r", "--token", "ghp_x"])
        assert result.exit_code == 0, result.output
        group = mock_live_update.call_args_list[-2].args[0]  # [-1] is the blank stop frame
        countdown, table = group.renderables
        assert table is mock_live_update.call_args_list[0].args[0]
        assert isinstance(countdown, RetryCountdown)

    @patch("dep_rank.core.dependents.scrape_dependents", new_callable=AsyncMock)
    def test_deps_verbose_keeps_log_line(self, mock_scrape: AsyncMock) -> None:
        mock_scrape.return_value = _scrape_result([])
        asyncio.run(
            cli_app.run_deps("https://github.com/o/r", 2, 0, False, False, "t", verbose=True)
        )
        assert mock_scrape.call_args.kwargs["on_retry"] is None  # scraper logs instead

    @patch("rich.live.Live.start")
    @patch("dep_rank.cli.app._renders_live", return_value=True)
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
    def test_search_verbose_has_no_progress_and_logs_retries(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        _renders_live: MagicMock,
        mock_live_start: MagicMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.return_value = _scrape_result([])
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/o/r", query="q", hits=[], searched_repos=0
        )
        args = ["-v", "search", "https://github.com/o/r", "q", "--token", "t"]
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert mock_scrape.call_args.kwargs["on_retry"] is None  # scraper logs instead
        mock_live_start.assert_not_called()  # no progress bar

    @patch("rich.progress.Progress.stop")
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
    def test_search_verbose_never_stops_a_progress(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_progress_stop: MagicMock,
        runner: CliRunner,
    ) -> None:
        # Rich < 14.3 prints a newline from a disabled Progress.stop() on non-TTY stderr.
        mock_scrape.return_value = _scrape_result([])
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/o/r", query="q", hits=[], searched_repos=0
        )
        args = ["-v", "search", "https://github.com/o/r", "q", "--token", "t"]
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        mock_progress_stop.assert_not_called()

    @patch("rich.progress.Progress.update")
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
    def test_search_shows_countdown_in_progress_then_clears(
        self,
        mock_scrape: AsyncMock,
        mock_search: AsyncMock,
        mock_progress_update: MagicMock,
        runner: CliRunner,
    ) -> None:
        mock_scrape.side_effect = _scrape_retrying_then_page
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/o/r", query="q", hits=[], searched_repos=0
        )
        result = runner.invoke(cli, ["search", "https://github.com/o/r", "q", "--token", "t"])
        assert result.exit_code == 0, result.output
        first, second = mock_progress_update.call_args_list
        assert isinstance(first.kwargs["retry"], RetryCountdown)
        assert second.kwargs["retry"] == ""


class TestRetryLogWithoutLiveTerminal:
    """Redirected stderr renders no live view, so each retry must still be logged."""

    @pytest.fixture(autouse=True)
    def _one_429(self, monkeypatch: pytest.MonkeyPatch, mock_http: FakeHTTP) -> None:
        monkeypatch.setattr("dep_rank.core.scraper.asyncio.sleep", AsyncMock())
        first = "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY"
        mock_http.get(first, status=429, headers={"Retry-After": "0"})
        mock_http.get(first, body=DEPENDENTS_HTML_LAST_PAGE)

    def test_deps(self, runner: CliRunner, caplog: pytest.LogCaptureFixture) -> None:
        result = runner.invoke(cli, ["deps", "https://github.com/owner/repo", "--token", "t"])
        assert result.exit_code == 0, result.output
        assert "Rate limited on page 1" in caplog.text

    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    def test_search(
        self, mock_search: AsyncMock, runner: CliRunner, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_search.return_value = CodeSearchResult(
            source="https://github.com/owner/repo", query="q", hits=[], searched_repos=0
        )
        args = ["search", "https://github.com/owner/repo", "q", "--token", "t"]
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "Rate limited on page 1" in caplog.text


class TestOnPageCallbacks:
    @patch("rich.progress.Progress.update")
    @patch("dep_rank.core.search.search_code", new_callable=AsyncMock)
    @patch("dep_rank.core.search.scrape_dependents", new_callable=AsyncMock)
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
        mock_progress_update.assert_called_once_with(
            ANY, completed=1, est_text="1/~30 estimated pages (3.33%)", retry=""
        )


class TestRankByTrust:
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


class TestEmptyDependents:
    """GitHub's parsed 0 header gets one message; an unparseable header changes nothing."""

    BASE = "https://github.com/owner/repo/network/dependents?dependent_type="

    @pytest.mark.parametrize(
        ("extra_args", "dep_type", "noun"),
        [
            pytest.param([], "REPOSITORY", "repositories", id="repositories"),
            pytest.param(["--packages"], "PACKAGE", "packages", id="packages"),
            pytest.param(
                ["--rank-by", "trust", "--trust-check"],
                "REPOSITORY",
                "repositories",
                id="trust-check",
            ),
        ],
    )
    def test_deps_reports_zero_instead_of_empty_table(
        self,
        runner: CliRunner,
        mock_http: FakeHTTP,
        extra_args: list[str],
        dep_type: str,
        noun: str,
    ) -> None:
        mock_http.get(self.BASE + dep_type, body=DEPENDENTS_HTML_EMPTY)
        result = runner.invoke(
            cli,
            ["deps", "https://github.com/owner/repo/", "--token", "ghp_x", *extra_args],
        )
        assert result.exit_code == 0
        assert f"GitHub reports 0 dependent {noun} for owner/repo." in result.stderr
        assert "https://github.com/owner/repo/network/dependents" in result.stderr
        assert "Scraped" not in result.output
        assert "star threshold" not in result.output
        assert "Trust check" not in result.output
        assert "Top dependents" not in result.output

    def test_deps_unparsed_header_keeps_summary(
        self, runner: CliRunner, mock_http: FakeHTTP
    ) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=DEPENDENTS_HTML_NO_HEADER)
        result = runner.invoke(cli, ["deps", "https://github.com/owner/repo", "--token", "ghp_x"])
        assert result.exit_code == 0
        assert "GitHub reports 0" not in result.output
        assert "Scraped 1 page" in result.stderr
        assert "delta/app" in result.stdout

    def test_search_reports_zero(self, runner: CliRunner, mock_http: FakeHTTP) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=DEPENDENTS_HTML_EMPTY)
        result = runner.invoke(
            cli,
            ["search", "https://github.com/owner/repo", "import os", "--token", "ghp_x"]
            + ["--max-pages", "20"],
        )
        assert result.exit_code == 0
        assert "GitHub reports 0 dependent repositories for owner/repo." in result.stderr
        assert "Scraped" not in result.output
        assert "pages (" not in result.output  # no leftover progress bar
        assert "No results found" not in result.output
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "args",
        [
            ["deps", "https://github.com/owner/repo", "--token", "ghp_x"],
            ["deps", "https://github.com/owner/repo", "--token", "ghp_x", "--format", "json"],
            ["search", "https://github.com/owner/repo", "import os", "--token", "ghp_x"],
        ],
        ids=["deps", "deps-json", "search"],
    )
    def test_missing_repository_exits_1(
        self, runner: CliRunner, mock_http: FakeHTTP, args: list[str]
    ) -> None:
        mock_http.get(self.BASE + "REPOSITORY", status=404)
        result = runner.invoke(cli, args)
        assert result.exit_code == 1
        assert "Error: repository not found (or private): owner/repo" in result.stderr
        assert result.stdout == ""
        assert isinstance(result.exception, SystemExit)  # no traceback

    MENU = [("pkg/default", "ID0", True), ("pkg/other", "ID1", False), ("pkg/third", "ID2", False)]

    @staticmethod
    def _flat(text: str) -> str:
        return " ".join(text.split())

    def test_deps_hints_at_other_packages(self, runner: CliRunner, mock_http: FakeHTTP) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=dependents_page([], repos=0, menu=self.MENU))
        result = runner.invoke(cli, ["deps", "https://github.com/owner/repo", "--token", "ghp_x"])
        assert result.exit_code == 0
        flat = self._flat(result.stderr)
        assert "Only package 'pkg/default' was scraped; this repository lists 3 packages" in flat
        assert "pkg/default, pkg/other, pkg/third" in flat
        assert "--package-name NAME" in flat
        assert "may not have linked" not in flat

    def test_deps_hint_follows_non_empty_results(
        self, runner: CliRunner, mock_http: FakeHTTP
    ) -> None:
        page = dependents_page([("delta", "app", 80)], menu=self.MENU)
        mock_http.get(self.BASE + "REPOSITORY", body=page)
        result = runner.invoke(cli, ["deps", "https://github.com/owner/repo", "--token", "ghp_x"])
        assert result.exit_code == 0
        assert "delta/app" in result.stdout
        assert "Only package 'pkg/default' was scraped" in self._flat(result.stderr)

    @pytest.mark.parametrize(
        ("menu", "extra"),
        [
            (
                [("pkg/default", "ID0", True), ("pkg/other", "ID1", False)],
                ["--package-name", "pkg/default"],
            ),
            ([("pkg/default", "ID0", True)], []),
        ],
        ids=["named", "single-package"],
    )
    def test_no_hint_with_package_name_or_single_package(
        self,
        runner: CliRunner,
        mock_http: FakeHTTP,
        menu: list[tuple[str, str, bool]],
        extra: list[str],
    ) -> None:
        mock_http.get(
            self.BASE + "REPOSITORY", body=dependents_page([("delta", "app", 80)], menu=menu)
        )
        result = runner.invoke(
            cli, ["deps", "https://github.com/owner/repo", "--token", "ghp_x", *extra]
        )
        assert result.exit_code == 0
        assert "Only package" not in self._flat(result.stderr)

    def test_search_hints_at_other_packages(self, runner: CliRunner, mock_http: FakeHTTP) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=dependents_page([], repos=0, menu=self.MENU))
        result = runner.invoke(
            cli, ["search", "https://github.com/owner/repo", "import os", "--token", "ghp_x"]
        )
        assert result.exit_code == 0
        assert "Only package 'pkg/default' was scraped" in self._flat(result.stderr)

    def test_search_package_name_scrapes_that_package(
        self, runner: CliRunner, mock_http: FakeHTTP
    ) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=dependents_page([], repos=0, menu=self.MENU))
        switched_menu = [("pkg/default", "ID0", False), ("pkg/other", "ID1", True)]
        switched = dependents_page([], repos=0, menu=switched_menu)
        mock_http.get(self.BASE + "REPOSITORY&package_id=ID1", body=switched)
        result = runner.invoke(
            cli,
            ["search", "https://github.com/owner/repo", "import os", "--token", "ghp_x"]
            + ["--package-name", "pkg/other"],
        )
        assert result.exit_code == 0
        assert len(mock_http.requests[("GET", self.BASE + "REPOSITORY&package_id=ID1")]) == 1

    @pytest.mark.parametrize("with_menu", [True, False], ids=["dropdown", "no-dropdown"])
    def test_deps_json_has_package_fields_only_with_dropdown(
        self, runner: CliRunner, mock_http: FakeHTTP, with_menu: bool
    ) -> None:
        menu = self.MENU if with_menu else []
        page = dependents_page([("delta", "app", 80)], menu=menu)
        mock_http.get(self.BASE + "REPOSITORY", body=page)
        args = ["deps", "https://github.com/owner/repo", "--token", "ghp_x", "--format", "json"]
        result = runner.invoke(cli, args)
        payload = json.loads(result.stdout)
        if with_menu:
            assert payload["package"] == "pkg/default"
            assert payload["packages"] == ["pkg/default", "pkg/other", "pkg/third"]
        else:
            assert "package" not in payload
            assert "packages" not in payload
        assert "Only package" not in result.stderr  # JSON mode prints no scrape outcome

    def test_unknown_package_name_exits_1(self, runner: CliRunner, mock_http: FakeHTTP) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=dependents_page([], menu=self.MENU))
        result = runner.invoke(
            cli,
            ["deps", "https://github.com/owner/repo", "--token", "ghp_x", "--package-name", "nope"],
        )
        assert result.exit_code == 1
        assert "Error: no package named 'nope'; available: pkg/default" in self._flat(result.stderr)

    def test_search_complete_scrape_has_no_max_pages_percentage(
        self, runner: CliRunner, mock_http: FakeHTTP
    ) -> None:
        mock_http.get(self.BASE + "REPOSITORY", body=DEPENDENTS_HTML_PAGE_1)
        mock_http.get(
            "https://github.com/owner/repo/network/dependents?page=2",
            body=DEPENDENTS_HTML_LAST_PAGE,
        )
        with patch(
            "dep_rank.core.search.search_code",
            new_callable=AsyncMock,
            return_value=CodeSearchResult(
                source="https://github.com/owner/repo", query="q", hits=[], searched_repos=3
            ),
        ):
            result = runner.invoke(
                cli,
                ["search", "https://github.com/owner/repo", "q", "--token", "ghp_x"]
                + ["--max-pages", "20"],
            )
        assert result.exit_code == 0
        assert "Scraped all 2 pages" in result.stderr
        assert "%" not in result.stderr  # the progress bar's /--max-pages share is gone


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["deps", "https://github.com/owner/repo", "--token", "t"], id="deps"),
        pytest.param(
            ["search", "https://github.com/owner/repo", "import os", "--token", "t"], id="search"
        ),
    ],
)
def test_requests_identify_installed_version(
    runner: CliRunner, mock_http: FakeHTTP, command: list[str]
) -> None:
    mock_http.get(
        "https://github.com/owner/repo/network/dependents?dependent_type=REPOSITORY",
        body=DEPENDENTS_HTML_EMPTY,
    )
    result = runner.invoke(cli, command)
    assert result.exit_code == 0, result.output
    assert mock_http.request_count == 1
    # Compared with the runtime version, so releases need no test edit.
    assert all(
        request.headers["User-Agent"] == f"dep-rank/{__version__}"
        for requests in mock_http.requests.values()
        for request in requests
    )


class TestMcpCommand:
    def test_missing_extra_prints_install_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "fastmcp", None)
        monkeypatch.delitem(sys.modules, "dep_rank.mcp.server", raising=False)
        result = CliRunner().invoke(cli, ["mcp"])
        assert result.exit_code == 1
        assert 'pip install "dep-rank[mcp]"' in result.stderr

    def test_runs_stdio_without_banner(self) -> None:
        with patch("dep_rank.mcp.server.mcp.run") as run:
            result = CliRunner().invoke(cli, ["mcp"])
        assert result.exit_code == 0
        run.assert_called_once_with(transport="stdio", show_banner=False)

    def test_unrelated_import_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Broken(ModuleType):
            def __getattr__(self, name: str) -> Any:
                raise ImportError("boom", name="dep_rank.mcp.helper")

        monkeypatch.setitem(sys.modules, "dep_rank.mcp.server", _Broken("dep_rank.mcp.server"))
        result = CliRunner().invoke(cli, ["mcp"])
        assert isinstance(result.exception, ImportError)
        assert result.exception.name == "dep_rank.mcp.helper"
        assert "dep-rank[mcp]" not in result.output
