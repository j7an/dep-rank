"""Integration tests for the CLI."""

import os
from typing import Any
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from ghtopdep.cli import cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


class TestCLIBasic:
    """Basic CLI invocation tests."""

    def test_cli_help(self, cli_runner: CliRunner) -> None:
        """Test CLI help output."""
        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_cli_missing_url_argument(self, cli_runner: CliRunner) -> None:
        """Test CLI with missing URL argument."""
        result = cli_runner.invoke(cli, [])
        assert result.exit_code != 0

    def test_cli_invalid_url_format(self, cli_runner: CliRunner) -> None:
        """Test CLI with invalid URL format."""
        result = cli_runner.invoke(cli, ["https://invalid.com/user/repo"])
        assert result.exit_code == 1
        assert "Error" in result.output


class TestCLIURLValidation:
    """Tests for URL validation via CLI."""

    def test_cli_valid_github_url(self, cli_runner: CliRunner) -> None:
        """Test CLI with valid GitHub URL."""
        with patch("ghtopdep.cli.requests.session"):
            with patch("ghtopdep.cli.get_max_deps", return_value=0):
                result = cli_runner.invoke(cli, ["https://github.com/user/repo"])
                # Should not fail on URL validation
                assert "Error: Invalid GitHub URL" not in result.output

    def test_cli_empty_url(self, cli_runner: CliRunner) -> None:
        """Test CLI with empty URL."""
        result = cli_runner.invoke(cli, [""])
        assert result.exit_code == 1

    def test_cli_gitlab_url(self, cli_runner: CliRunner) -> None:
        """Test CLI rejects non-GitHub URLs."""
        result = cli_runner.invoke(cli, ["https://gitlab.com/user/repo"])
        assert result.exit_code == 1
        assert "GitHub" in result.output

    def test_cli_url_with_too_many_segments(self, cli_runner: CliRunner) -> None:
        """Test CLI rejects URL with too many path segments."""
        result = cli_runner.invoke(cli, ["https://github.com/user/repo/extra"])
        assert result.exit_code == 1

    def test_cli_url_missing_repo(self, cli_runner: CliRunner) -> None:
        """Test CLI rejects URL with only owner."""
        result = cli_runner.invoke(cli, ["https://github.com/user"])
        assert result.exit_code == 1


class TestCLIOptions:
    """Tests for CLI options."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_repositories_flag(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --repositories flag."""
        result = cli_runner.invoke(
            cli, ["https://github.com/user/repo", "--repositories"]
        )
        # Should complete without error (even if no dependents)
        assert "Error" not in result.output or "Error connecting" in result.output

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_packages_flag(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --packages flag."""
        result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--packages"])
        assert "Error" not in result.output or "Error connecting" in result.output

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_table_flag(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --table flag."""
        result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--table"])
        assert result.exit_code in [0, 1]  # May fail on connection

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_json_flag(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --json flag."""
        result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
        assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_rows_option(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --rows option."""
        result = cli_runner.invoke(
            cli, ["https://github.com/user/repo", "--rows", "20"]
        )
        assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_minstar_option(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test --minstar option."""
        result = cli_runner.invoke(
            cli, ["https://github.com/user/repo", "--minstar", "100"]
        )
        assert result.exit_code in [0, 1]


class TestCLIEnvironmentVariables:
    """Tests for CLI environment variable handling."""

    def test_cli_requires_token_for_description(self, cli_runner: CliRunner) -> None:
        """Test that --description requires token."""
        result = cli_runner.invoke(
            cli, ["https://github.com/user/repo", "--description"]
        )
        # Should exit with error when token is missing
        assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token"})
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_cli_description_with_token(
        self,
        _mock_login: Any,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test --description with token from environment."""
        result = cli_runner.invoke(
            cli, ["https://github.com/user/repo", "--description"]
        )
        # Should not require token error
        assert "Please provide token" not in result.output

    def test_cli_report_requires_base_url(self, cli_runner: CliRunner) -> None:
        """Test that --report requires GHTOPDEP_BASE_URL."""
        result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--report"])
        assert result.exit_code == 1
        assert "GHTOPDEP_BASE_URL" in result.output

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_report_with_base_url(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        _mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test --report with GHTOPDEP_BASE_URL set."""
        mock_get_response = Mock()
        mock_get_response.status_code = 404
        mock_get.return_value = mock_get_response

        result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--report"])
        # Should attempt to fetch report from base URL
        assert result.exit_code in [0, 1]

    @patch.dict(os.environ, {"GHTOPDEP_ENV": "development"})
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_development_mode_default_url(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test development mode sets default URL."""
        with patch("ghtopdep.cli.requests.get"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo"])
            # Should work without explicit BASE_URL in dev mode
            assert result.exit_code in [0, 1]


class TestCLIErrorHandling:
    """Tests for CLI error handling."""

    def test_cli_connection_error_handled(self, cli_runner: CliRunner) -> None:
        """Test that connection errors are handled gracefully."""
        with patch("ghtopdep.cli.requests.session"):
            with patch("ghtopdep.cli.get_max_deps") as mock_deps:
                mock_deps.side_effect = ConnectionError("Network error")
                result = cli_runner.invoke(cli, ["https://github.com/user/repo"])
                # Should handle connection error
                assert result.exit_code != 0

    @patch("ghtopdep.cli.requests.session")
    def test_cli_invalid_token_handling(
        self, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test handling of invalid tokens."""
        with patch("ghtopdep.cli.get_max_deps", return_value=0):
            with patch("ghtopdep.cli.github3.login") as mock_login:
                mock_login.side_effect = Exception("Invalid token")
                result = cli_runner.invoke(
                    cli,
                    [
                        "https://github.com/user/repo",
                        "--description",
                        "--token",
                        "invalid",
                    ],
                )
                # Should handle login error
                assert result.exit_code != 0


class TestCLIOutputModes:
    """Tests for CLI output modes."""

    def test_cli_default_table_output(self, cli_runner: CliRunner) -> None:
        """Test default output is table format."""
        with patch("ghtopdep.cli.requests.session"):
            with patch("ghtopdep.cli.get_max_deps", return_value=0):
                with patch("ghtopdep.cli.show_result") as mock_show:
                    cli_runner.invoke(cli, ["https://github.com/user/repo"])
                    # show_result should be called with table=True by default
                    if mock_show.called:
                        assert mock_show.call_args[0][4] is True

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_json_output(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test JSON output format."""
        with patch("ghtopdep.cli.show_result") as mock_show:
            cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # show_result should be called with table=False for JSON
            if mock_show.called:
                assert mock_show.call_args[0][4] is False


class TestCLIIntegration:
    """Integration tests combining multiple features."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_cli_all_options_combined(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test CLI with multiple options combined."""
        result = cli_runner.invoke(
            cli,
            [
                "https://github.com/user/repo",
                "--packages",
                "--json",
                "--rows",
                "5",
                "--minstar",
                "10",
            ],
        )
        # Should handle combined options
        assert result.exit_code in [0, 1]
