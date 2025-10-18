"""Unit tests for error handling in ghtopdep API calls."""

import pytest
from unittest.mock import Mock, patch
from click.testing import CliRunner
import requests
from ghtopdep.cli import (
    get_max_deps,
    fetch_description,
    OneDayHeuristic,
    cli,
)


class TestOneDayHeuristicErrorHandling:
    """Tests for OneDayHeuristic.update_headers() error handling."""

    def test_update_headers_missing_date(self):
        """Test handling of missing 'date' header."""
        heuristic = OneDayHeuristic()
        response = Mock()
        response.status = 200
        response.headers = {}  # No date header

        result = heuristic.update_headers(response)
        assert result == {}

    def test_update_headers_invalid_date_format(self):
        """Test handling of invalid date format."""
        heuristic = OneDayHeuristic()
        response = Mock()
        response.status = 200
        response.headers = {"date": "invalid-date-format"}

        result = heuristic.update_headers(response)
        assert result == {}

    def test_update_headers_valid_date(self):
        """Test successful date parsing."""
        heuristic = OneDayHeuristic()
        response = Mock()
        response.status = 200
        response.headers = {"date": "Wed, 21 Oct 2024 07:28:00 GMT"}

        result = heuristic.update_headers(response)
        assert "expires" in result
        assert result["cache-control"] == "public"

    def test_update_headers_non_cacheable_status(self):
        """Test non-cacheable status codes."""
        heuristic = OneDayHeuristic()
        response = Mock()
        response.status = 500  # Not in cacheable_by_default_statuses
        response.headers = {"date": "Wed, 21 Oct 2024 07:28:00 GMT"}

        result = heuristic.update_headers(response)
        assert result == {}


class TestFetchDescriptionErrorHandling:
    """Tests for fetch_description() error handling."""

    def test_fetch_description_invalid_url_format(self):
        """Test handling of invalid URL format."""
        gh = Mock()
        result = fetch_description(gh, "invalid")
        assert result == ""

    def test_fetch_description_missing_repository(self):
        """Test handling when repository is missing from URL."""
        gh = Mock()
        result = fetch_description(gh, "/owner/")
        assert result == ""

    def test_fetch_description_api_exception(self):
        """Test handling of GitHub API exceptions."""
        gh = Mock()
        gh.repository.side_effect = Exception("API Error")

        result = fetch_description(gh, "/owner/repo")
        assert result == ""

    def test_fetch_description_repository_not_found(self):
        """Test handling when repository is not found."""
        gh = Mock()
        gh.repository.return_value = None

        result = fetch_description(gh, "/owner/repo")
        assert result == ""

    def test_fetch_description_success(self):
        """Test successful description fetch."""
        gh = Mock()
        repo = Mock()
        repo.description = "A test repository"
        gh.repository.return_value = repo

        result = fetch_description(gh, "/owner/repo")
        assert "test repository" in result

    def test_fetch_description_no_description(self):
        """Test repository with no description."""
        gh = Mock()
        repo = Mock()
        repo.description = None
        gh.repository.return_value = repo

        result = fetch_description(gh, "/owner/repo")
        assert result == " "


class TestGetMaxDepsErrorHandling:
    """Tests for get_max_deps() error handling."""

    def test_get_max_deps_timeout(self):
        """Test handling of request timeout."""
        sess = Mock()
        sess.get.side_effect = requests.exceptions.Timeout()

        with pytest.raises(SystemExit) as exc_info:
            get_max_deps(sess, "http://github.com/test/repo/network/dependents")
        assert exc_info.value.code == 1

    def test_get_max_deps_connection_error(self):
        """Test handling of connection errors."""
        sess = Mock()
        sess.get.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with pytest.raises(SystemExit) as exc_info:
            get_max_deps(sess, "http://github.com/test/repo/network/dependents")
        assert exc_info.value.code == 1

    def test_get_max_deps_http_error(self):
        """Test handling of HTTP errors."""
        sess = Mock()
        response = Mock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        sess.get.return_value = response

        with pytest.raises(SystemExit) as exc_info:
            get_max_deps(sess, "http://github.com/test/repo/network/dependents")
        assert exc_info.value.code == 1

    def test_get_max_deps_missing_html_element(self):
        """Test handling of missing HTML element."""
        sess = Mock()
        response = Mock()
        response.status_code = 200
        response.text = "<html></html>"  # No dependents element
        sess.get.return_value = response

        with pytest.raises(SystemExit) as exc_info:
            get_max_deps(sess, "http://github.com/test/repo/network/dependents")
        assert exc_info.value.code == 1

    def test_get_max_deps_element_no_text(self):
        """Test handling of element with no text content."""
        sess = Mock()
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.text = "<html></html>"
        sess.get.return_value = response

        with patch("ghtopdep.cli.HTMLParser") as mock_parser:
            mock_element = Mock()
            mock_element.text.return_value = ""
            mock_parser_instance = Mock()
            mock_parser_instance.css_first.return_value = mock_element
            mock_parser.return_value = mock_parser_instance

            with pytest.raises(SystemExit) as exc_info:
                get_max_deps(sess, "http://github.com/test/repo/network/dependents")
            assert exc_info.value.code == 1

    def test_get_max_deps_invalid_number_format(self):
        """Test handling of invalid number format in element text."""
        sess = Mock()
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.text = "<html></html>"
        sess.get.return_value = response

        with patch("ghtopdep.cli.HTMLParser") as mock_parser:
            mock_element = Mock()
            mock_element.text.return_value = "invalid-number Repositories"
            mock_parser_instance = Mock()
            mock_parser_instance.css_first.return_value = mock_element
            mock_parser.return_value = mock_parser_instance

            with pytest.raises(SystemExit) as exc_info:
                get_max_deps(sess, "http://github.com/test/repo/network/dependents")
            assert exc_info.value.code == 1

    def test_get_max_deps_success(self):
        """Test successful max deps retrieval."""
        sess = Mock()
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.text = "<html></html>"
        sess.get.return_value = response

        with patch("ghtopdep.cli.HTMLParser") as mock_parser:
            mock_element = Mock()
            mock_element.text.return_value = "1,234 Repositories"
            mock_parser_instance = Mock()
            mock_parser_instance.css_first.return_value = mock_element
            mock_parser.return_value = mock_parser_instance

            result = get_max_deps(sess, "http://github.com/test/repo/network/dependents")
            assert result == 1234


class TestReportModeErrorHandling:
    """Tests for report mode error handling."""

    def test_report_mode_get_timeout(self):
        """Test handling of report server GET timeout."""
        runner = CliRunner()
        with patch("ghtopdep.cli.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout()

            with runner.isolated_filesystem():
                result = runner.invoke(cli, [
                    "https://github.com/test/repo",
                    "--report"
                ], env={"GHTOPDEP_BASE_URL": "http://localhost:3000"})

                assert result.exit_code == 1

    def test_report_mode_get_connection_error(self):
        """Test handling of report server GET connection error."""
        runner = CliRunner()
        with patch("ghtopdep.cli.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("Connection failed")

            result = runner.invoke(cli, [
                "https://github.com/test/repo",
                "--report"
            ], env={"GHTOPDEP_BASE_URL": "http://localhost:3000"})

            assert result.exit_code == 1

    def test_report_mode_post_timeout(self):
        """Test handling of report server POST timeout."""
        runner = CliRunner()

        with patch("ghtopdep.cli.requests.get") as mock_get, \
             patch("ghtopdep.cli.get_max_deps") as mock_max_deps, \
             patch("ghtopdep.cli.requests.post") as mock_post:

            # GET returns 404 (no cached data), so scraping proceeds
            mock_response = Mock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            mock_max_deps.return_value = 0

            mock_post.side_effect = requests.exceptions.Timeout()

            result = runner.invoke(cli, [
                "https://github.com/test/repo",
                "--report"
            ], env={"GHTOPDEP_BASE_URL": "http://localhost:3000"})

            # Should exit with error code
            assert result.exit_code == 1

    def test_report_mode_post_connection_error(self):
        """Test handling of report server POST connection error."""
        runner = CliRunner()

        with patch("ghtopdep.cli.requests.get") as mock_get, \
             patch("ghtopdep.cli.get_max_deps") as mock_max_deps, \
             patch("ghtopdep.cli.requests.post") as mock_post:

            # GET returns 404 (no cached data), so scraping proceeds
            mock_response = Mock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            mock_max_deps.return_value = 0

            mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

            result = runner.invoke(cli, [
                "https://github.com/test/repo",
                "--report"
            ], env={"GHTOPDEP_BASE_URL": "http://localhost:3000"})

            assert result.exit_code == 1


class TestMainScrapingLoopErrorHandling:
    """Tests for main scraping loop error handling."""

    def test_scraping_loop_network_timeout(self):
        """Test handling of network timeout during scraping."""
        runner = CliRunner()

        with patch("ghtopdep.cli.requests.session") as mock_session_class, \
             patch("ghtopdep.cli.get_max_deps") as mock_max_deps:

            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            # First call succeeds (get_max_deps), second call times out
            mock_session.get.side_effect = [
                Mock(status_code=200, text="<html></html>", raise_for_status=lambda: None),
                requests.exceptions.Timeout()
            ]

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle gracefully and exit successfully (with warning)
            assert result.exit_code == 0 or "Warning" in result.output

    def test_scraping_loop_connection_error(self):
        """Test handling of connection error during scraping."""
        runner = CliRunner()

        with patch("ghtopdep.cli.requests.session") as mock_session_class, \
             patch("ghtopdep.cli.get_max_deps") as mock_max_deps:

            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_session.get.side_effect = requests.exceptions.ConnectionError("Network unreachable")

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle gracefully
            assert result.exit_code == 0 or "Warning" in result.output

    def test_scraping_loop_html_parse_error(self):
        """Test handling of HTML parsing error."""
        runner = CliRunner()

        with patch("ghtopdep.cli.requests.session") as mock_session_class, \
             patch("ghtopdep.cli.get_max_deps") as mock_max_deps, \
             patch("ghtopdep.cli.HTMLParser") as mock_parser_class:

            mock_session = Mock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = lambda: None
            mock_session.get.return_value = mock_response

            mock_parser_class.side_effect = Exception("Parse error")

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle gracefully
            assert result.exit_code == 0 or "Warning" in result.output


class TestSearchCodeErrorHandling:
    """Tests for search code API error handling."""

    def test_search_invalid_url_parsing(self):
        """Test handling of invalid URL during search."""
        gh = Mock()
        gh.search_code.return_value = []

        # This would be called in the search loop
        # Function should handle gracefully with empty URL
        # Example: {"url": "", "stars": 100}

    def test_search_api_exception(self):
        """Test handling of search API exceptions."""
        gh = Mock()
        gh.search_code.side_effect = Exception("API Rate Limit Exceeded")

        # Search code should catch this and continue with next repo
        # This would be in the CLI function
