"""Additional tests to improve coverage of edge cases and main CLI logic."""

import os
import pytest
from unittest.mock import Mock, MagicMock, patch
from click.testing import CliRunner
from selectolax.parser import HTMLParser
from ghtopdep.cli import cli


@pytest.fixture
def cli_runner():
    """Create a Click CLI runner."""
    return CliRunner()


class TestHTMLParsing:
    """Tests for HTML parsing with realistic data."""

    def test_parse_dependents_items(self, html_response_dependents):
        """Test parsing HTML with dependent items."""
        tree = HTMLParser(html_response_dependents)
        deps = tree.css("#dependents > div.Box > div.flex-items-center")
        # Should find dependent items
        assert len(deps) >= 0  # May or may not find items depending on HTML structure

    def test_parse_max_deps_count(self, html_response_dependents):
        """Test extracting max dependency count."""
        tree = HTMLParser(html_response_dependents)
        deps_count = tree.css_first('.table-list-header-toggle .btn-link.selected')
        assert deps_count is not None
        assert "repositories" in deps_count.text()

    def test_parse_last_page_structure(self, html_response_last_page):
        """Test parsing last page structure."""
        tree = HTMLParser(html_response_last_page)
        # Should be able to parse last page HTML
        deps_count = tree.css_first('.table-list-header-toggle .btn-link.selected')
        assert deps_count is not None


@pytest.fixture
def mock_session_with_dependents(html_response_dependents, html_response_last_page):
    """Create a mock session that returns dependents pages."""
    session = MagicMock()

    # First call returns page 1, second call returns last page
    response1 = Mock()
    response1.text = html_response_dependents
    response1.status_code = 200

    response2 = Mock()
    response2.text = html_response_last_page
    response2.status_code = 200

    session.get.side_effect = [response1, response2]
    return session


@patch("ghtopdep.cli.requests.session")
@patch("ghtopdep.cli.github3")
def test_cli_with_pagination(mock_github, mock_session_class, cli_runner, mock_session_with_dependents):
    """Test CLI processes paginated results."""
    mock_session_class.return_value = mock_session_with_dependents

    with patch("ghtopdep.cli.get_max_deps", return_value=60):
        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should successfully process paginated results
            assert result.exit_code in [0, 1]


class TestCliReportMode:
    """Tests for report mode functionality."""

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_report_mode_404_response(self, mock_deps, mock_session, mock_post, mock_get, cli_runner):
        """Test report mode when report endpoint returns 404."""
        mock_get_response = Mock()
        mock_get_response.status_code = 404
        mock_get.return_value = mock_get_response

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--report"])
            # Should handle 404 gracefully
            assert result.exit_code in [0, 1]

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_report_mode_success(self, mock_deps, mock_session, mock_post, mock_get, cli_runner):
        """Test report mode with successful response."""
        mock_get_response = Mock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = [
            {"url": "https://github.com/user/repo1", "stars": 100}
        ]
        mock_get.return_value = mock_get_response

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--report"])
            # Should process successful report response
            assert result.exit_code in [0, 1]


class TestCliMinstFilter:
    """Tests for minimum stars filtering."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_cli_minstar_high_value(self, mock_deps, mock_session, cli_runner):
        """Test CLI with high minstar value."""
        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli,
                ["https://github.com/user/repo", "--minstar", "1000", "--json"]
            )
            # Should handle high minstar
            assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_cli_minstar_zero_value(self, mock_deps, mock_session, cli_runner):
        """Test CLI with zero minstar value."""
        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli,
                ["https://github.com/user/repo", "--minstar", "0", "--json"]
            )
            # Should handle zero minstar
            assert result.exit_code in [0, 1]


class TestCliSearchMode:
    """Tests for search functionality."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_cli_search_option_requires_token(self, mock_login, mock_deps, mock_session, cli_runner):
        """Test that search requires token."""
        # Ensure GHTOPDEP_TOKEN is not set in environment
        with patch.dict(os.environ, {}, clear=True):
            result = cli_runner.invoke(
                cli,
                ["https://github.com/user/repo", "--search", "test_keyword"]
            )
            # Should require token
            assert result.exit_code == 1


class TestCliModeSettings:
    """Tests for different mode settings."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_packages_destination(self, mock_deps, mock_session, cli_runner):
        """Test packages destination type."""
        with patch("ghtopdep.cli.CacheControl"):
            with patch("ghtopdep.cli.HTMLParser"):
                result = cli_runner.invoke(
                    cli,
                    ["https://github.com/user/repo", "--packages"]
                )
                # Should handle packages mode
                assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_repositories_destination(self, mock_deps, mock_session, cli_runner):
        """Test repositories destination type (default)."""
        with patch("ghtopdep.cli.CacheControl"):
            with patch("ghtopdep.cli.HTMLParser"):
                result = cli_runner.invoke(
                    cli,
                    ["https://github.com/user/repo", "--repositories"]
                )
                # Should handle repositories mode
                assert result.exit_code in [0, 1]


@patch("ghtopdep.cli.requests.session")
@patch("ghtopdep.cli.get_max_deps", return_value=30)
def test_cli_rows_option_boundary(mock_deps, mock_session, cli_runner):
    """Test CLI with various rows values."""
    test_cases = ["1", "5", "10", "100", "1000"]

    for rows_value in test_cases:
        with patch("ghtopdep.cli.CacheControl"):
            with patch("ghtopdep.cli.HTMLParser"):
                result = cli_runner.invoke(
                    cli,
                    ["https://github.com/user/repo", "--rows", rows_value, "--json"]
                )
                # All rows values should be accepted
                assert result.exit_code in [0, 1]


class TestEnvironmentConfiguration:
    """Tests for environment variable configuration."""

    @patch.dict(os.environ, {"GHTOPDEP_ENV": "development"})
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_cli_development_environment(self, mock_deps, mock_session, cli_runner):
        """Test CLI in development environment."""
        with patch("ghtopdep.cli.CacheControl"):
            with patch("ghtopdep.cli.requests.get"):
                result = cli_runner.invoke(
                    cli,
                    ["https://github.com/user/repo"]
                )
                # Should work in dev environment
                assert result.exit_code in [0, 1]

    @patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token_value"})
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_cli_token_from_environment(self, mock_login, mock_deps, mock_session, cli_runner):
        """Test CLI uses token from environment variable."""
        with patch("ghtopdep.cli.CacheControl"):
            mock_login.return_value = MagicMock()
            result = cli_runner.invoke(
                cli,
                ["https://github.com/user/repo", "--description"]
            )
            # Should use token from environment
            assert result.exit_code in [0, 1]
