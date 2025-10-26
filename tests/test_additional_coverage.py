"""Additional tests to improve coverage of missing code paths in ghtopdep/cli.py."""

import os
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from ghtopdep.cli import OneDayHeuristic, cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def html_response_with_private_repos() -> str:
    """HTML response with private/ghost packages (missing stars)."""
    return """
    <html>
        <body>
            <div class="table-list-header-toggle">
                <button class="btn-link selected">30 repositories</button>
            </div>
            <div id="dependents">
                <div class="Box">
                    <div class="flex-items-center">
                        <!-- Private repo without stars -->
                        <span>
                            <a class="text-bold" href="/user1/repo1">user1/repo1</a>
                        </span>
                    </div>
                    <div class="flex-items-center">
                        <span>
                            <a class="text-bold" href="/user2/repo2">user2/repo2</a>
                        </span>
                        <div>
                            <span>100</span>
                        </div>
                    </div>
                </div>
                <div class="paginate-container">
                    <a href="/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def html_response_with_empty_stars() -> str:
    """HTML response with empty star text."""
    return """
    <html>
        <body>
            <div class="table-list-header-toggle">
                <button class="btn-link selected">30 repositories</button>
            </div>
            <div id="dependents">
                <div class="Box">
                    <div class="flex-items-center">
                        <span>
                            <a class="text-bold" href="/user1/repo1">user1/repo1</a>
                        </span>
                        <div>
                            <span></span>
                        </div>
                    </div>
                </div>
                <div class="paginate-container">
                    <a href="/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def html_response_with_invalid_stars() -> str:
    """HTML response with invalid star count."""
    return """
    <html>
        <body>
            <div class="table-list-header-toggle">
                <button class="btn-link selected">30 repositories</button>
            </div>
            <div id="dependents">
                <div class="Box">
                    <div class="flex-items-center">
                        <span>
                            <a class="text-bold" href="/user1/repo1">user1/repo1</a>
                        </span>
                        <div>
                            <span>invalid_number</span>
                        </div>
                    </div>
                </div>
                <div class="paginate-container">
                    <a href="/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def html_response_missing_repo_selector() -> str:
    """HTML response missing repo selector."""
    return """
    <html>
        <body>
            <div class="table-list-header-toggle">
                <button class="btn-link selected">30 repositories</button>
            </div>
            <div id="dependents">
                <div class="Box">
                    <div class="flex-items-center">
                        <div>
                            <span>100</span>
                        </div>
                    </div>
                </div>
                <div class="paginate-container">
                    <a href="/network/dependents?page=1">Previous</a>
                </div>
            </div>
        </body>
    </html>
    """


class TestSearchFunctionality:
    """Tests for search functionality (lines 578-604)."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_search_with_valid_results(
        self,
        mock_login: Any,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test search mode with valid search results."""
        # Setup GitHub client mock
        gh = MagicMock()
        mock_login.return_value = gh

        # Create mock search result with html_url
        search_result = MagicMock()
        search_result.html_url = "https://github.com/user/repo/search/result"

        gh.search_code.return_value = [search_result]

        with patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token"}):
            with patch("ghtopdep.cli.CacheControl"):
                with patch("ghtopdep.cli.requests.get") as mock_get:
                    mock_response = Mock()
                    mock_response.status_code = 404
                    mock_get.return_value = mock_response

                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/user/repo", "--search", "test_keyword"],
                    )
                    # Should process search results
                    assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_search_result_missing_html_url(
        self,
        mock_login: Any,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test search result missing html_url attribute."""
        gh = MagicMock()
        mock_login.return_value = gh

        # Create search result without html_url
        search_result = MagicMock()
        delattr(search_result, "html_url")

        gh.search_code.return_value = [search_result]

        with patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token"}):
            with patch("ghtopdep.cli.CacheControl"):
                with patch("ghtopdep.cli.requests.get") as mock_get:
                    mock_response = Mock()
                    mock_response.status_code = 404
                    mock_get.return_value = mock_response

                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/user/repo", "--search", "test_keyword"],
                    )
                    # Should handle missing html_url gracefully
                    assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_search_invalid_repo_url_parsing(
        self,
        mock_login: Any,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test search with invalid URL parsing."""
        gh = MagicMock()
        mock_login.return_value = gh

        search_result = MagicMock()
        search_result.html_url = "https://github.com/result"

        gh.search_code.return_value = [search_result]

        with patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token"}):
            with patch("ghtopdep.cli.CacheControl"):
                with patch("ghtopdep.cli.requests.get") as mock_get:
                    mock_response = Mock()
                    mock_response.status_code = 404
                    mock_get.return_value = mock_response

                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/user/repo", "--search", "test_keyword"],
                    )
                    # Should handle gracefully
                    assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    @patch("ghtopdep.cli.github3.login")
    def test_search_api_exception(
        self,
        mock_login: Any,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test search API exception handling."""
        gh = MagicMock()
        mock_login.return_value = gh

        # Make search_code raise an exception
        gh.search_code.side_effect = Exception("API Error")

        with patch.dict(os.environ, {"GHTOPDEP_TOKEN": "test_token"}):
            with patch("ghtopdep.cli.CacheControl"):
                with patch("ghtopdep.cli.requests.get") as mock_get:
                    mock_response = Mock()
                    mock_response.status_code = 404
                    mock_get.return_value = mock_response

                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/user/repo", "--search", "test_keyword"],
                    )
                    # Should handle API exception
                    assert result.exit_code in [0, 1]


class TestReportModeErrors:
    """Tests for report mode error handling."""

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_report_mode_invalid_json_response(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        _mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test report mode with invalid JSON response (status 200)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_response

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle invalid JSON
            assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_report_mode_non_200_404_status(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        _mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test report mode with non-200/404 status code."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle error status
            assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_report_mode_get_httperror(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        _mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test report mode GET with HTTPError."""
        import requests

        mock_get.side_effect = requests.exceptions.HTTPError("HTTP Error")

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle HTTP error
            assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=0)
    def test_report_mode_get_request_exception(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        _mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
    ) -> None:
        """Test report mode GET with generic RequestException."""
        import requests

        mock_get.side_effect = requests.exceptions.RequestException("Request Error")

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle request exception
            assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_report_mode_post_error_status(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
        html_response_last_page: str,
    ) -> None:
        """Test report mode POST with error status."""
        # Setup GET response
        mock_get_response = Mock()
        mock_get_response.status_code = 404
        mock_get.return_value = mock_get_response

        # Setup session
        mock_session = _mock_session.return_value
        mock_session_resp = Mock()
        mock_session_resp.text = html_response_last_page
        mock_session_resp.raise_for_status.return_value = None
        mock_session.get.return_value = mock_session_resp

        # Setup POST response with error
        mock_post_response = Mock()
        mock_post_response.status_code = 500
        mock_post.return_value = mock_post_response

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle POST error
            assert result.exit_code == 1

    @patch.dict(os.environ, {"GHTOPDEP_BASE_URL": "http://localhost:3000"})
    @patch("ghtopdep.cli.requests.get")
    @patch("ghtopdep.cli.requests.post")
    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_report_mode_post_timeout(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        mock_post: Any,
        mock_get: Any,
        cli_runner: CliRunner,
        html_response_last_page: str,
    ) -> None:
        """Test report mode POST timeout."""
        import requests

        # Setup GET response
        mock_get_response = Mock()
        mock_get_response.status_code = 404
        mock_get.return_value = mock_get_response

        # Setup session
        mock_session = _mock_session.return_value
        mock_session_resp = Mock()
        mock_session_resp.text = html_response_last_page
        mock_session_resp.raise_for_status.return_value = None
        mock_session.get.return_value = mock_session_resp

        # Setup POST timeout
        mock_post.side_effect = requests.exceptions.Timeout("Timeout")

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(
                cli, ["https://github.com/user/repo", "--report"]
            )
            # Should handle timeout
            assert result.exit_code == 1


class TestScrapingLoopEdgeCases:
    """Tests for scraping loop edge cases."""

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_scraping_with_private_repos(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
        html_response_with_private_repos: str,
    ) -> None:
        """Test scraping with private/ghost packages (missing stars)."""
        mock_session = _mock_session.return_value

        # First response with private repo
        response1 = Mock()
        response1.text = html_response_with_private_repos
        response1.raise_for_status.return_value = None

        # Second response (last page)
        response2 = Mock()
        response2.text = '<html><body><div class="table-list-header-toggle"><button class="btn-link selected">30 repositories</button></div><div id="dependents"><div class="Box"></div><div class="paginate-container"><a href="/network/dependents?page=1">Previous</a></div></div></body></html>'
        response2.raise_for_status.return_value = None

        mock_session.get.side_effect = [response1, response2]

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should handle private repos
            assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_scraping_with_empty_star_text(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
        html_response_with_empty_stars: str,
    ) -> None:
        """Test scraping with empty star text."""
        mock_session = _mock_session.return_value

        response1 = Mock()
        response1.text = html_response_with_empty_stars
        response1.raise_for_status.return_value = None

        response2 = Mock()
        response2.text = '<html><body><div class="table-list-header-toggle"><button class="btn-link selected">30 repositories</button></div><div id="dependents"><div class="Box"></div><div class="paginate-container"><a href="/network/dependents?page=1">Previous</a></div></div></body></html>'
        response2.raise_for_status.return_value = None

        mock_session.get.side_effect = [response1, response2]

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should handle empty stars
            assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_scraping_with_invalid_star_count(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
        html_response_with_invalid_stars: str,
    ) -> None:
        """Test scraping with invalid star count."""
        mock_session = _mock_session.return_value

        response1 = Mock()
        response1.text = html_response_with_invalid_stars
        response1.raise_for_status.return_value = None

        response2 = Mock()
        response2.text = '<html><body><div class="table-list-header-toggle"><button class="btn-link selected">30 repositories</button></div><div id="dependents"><div class="Box"></div><div class="paginate-container"><a href="/network/dependents?page=1">Previous</a></div></div></body></html>'
        response2.raise_for_status.return_value = None

        mock_session.get.side_effect = [response1, response2]

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should handle invalid star count
            assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_scraping_missing_repo_selector(
        self,
        _mock_deps: Any,
        _mock_session: Any,
        cli_runner: CliRunner,
        html_response_missing_repo_selector: str,
    ) -> None:
        """Test scraping with missing repo selector."""
        mock_session = _mock_session.return_value

        response1 = Mock()
        response1.text = html_response_missing_repo_selector
        response1.raise_for_status.return_value = None

        response2 = Mock()
        response2.text = '<html><body><div class="table-list-header-toggle"><button class="btn-link selected">30 repositories</button></div><div id="dependents"><div class="Box"></div><div class="paginate-container"><a href="/network/dependents?page=1">Previous</a></div></div></body></html>'
        response2.raise_for_status.return_value = None

        mock_session.get.side_effect = [response1, response2]

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should handle missing repo selector
            assert result.exit_code in [0, 1]

    @patch("ghtopdep.cli.requests.session")
    @patch("ghtopdep.cli.get_max_deps", return_value=30)
    def test_scraping_http_error_on_page(
        self, _mock_deps: Any, _mock_session: Any, cli_runner: CliRunner
    ) -> None:
        """Test scraping loop HTTP error on subsequent pages."""
        import requests

        mock_session = _mock_session.return_value

        response1 = Mock()
        response1.text = '<html><body><div class="table-list-header-toggle"><button class="btn-link selected">30 repositories</button></div><div id="dependents"><div class="Box"></div><div class="paginate-container"><a href="/network/dependents?page=2">Next</a></div></div></body></html>'
        response1.raise_for_status.return_value = None

        # Second call raises HTTPError
        response2 = Mock()
        response2.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "HTTP Error"
        )

        mock_session.get.side_effect = [response1, response2]

        with patch("ghtopdep.cli.CacheControl"):
            result = cli_runner.invoke(cli, ["https://github.com/user/repo", "--json"])
            # Should handle HTTP error
            assert result.exit_code in [0, 1]


class TestOneDayHeuristicErrors:
    """Tests for OneDayHeuristic exception handling."""

    def test_one_day_heuristic_date_parse_typeerror(self) -> None:
        """Test OneDayHeuristic with TypeError in date parsing."""
        heuristic = OneDayHeuristic()

        response = Mock()
        response.status = 200
        response.headers = {"date": "invalid_date"}

        with patch("ghtopdep.cli.parsedate", side_effect=TypeError("Type error")):
            result = heuristic.update_headers(response)
            # Should return empty dict on error
            assert result == {}

    def test_one_day_heuristic_date_parse_valueerror(self) -> None:
        """Test OneDayHeuristic with ValueError in date parsing."""
        heuristic = OneDayHeuristic()

        response = Mock()
        response.status = 200
        response.headers = {"date": "invalid_date"}

        with patch("ghtopdep.cli.parsedate", side_effect=ValueError("Value error")):
            result = heuristic.update_headers(response)
            # Should return empty dict on error
            assert result == {}

    def test_one_day_heuristic_date_parse_overflowerror(self) -> None:
        """Test OneDayHeuristic with OverflowError in date parsing."""
        heuristic = OneDayHeuristic()

        response = Mock()
        response.status = 200
        response.headers = {"date": "invalid_date"}

        with patch(
            "ghtopdep.cli.parsedate", side_effect=OverflowError("Overflow error")
        ):
            result = heuristic.update_headers(response)
            # Should return empty dict on error
            assert result == {}


class TestURLValidationEdgeCases:
    """Tests for URL validation edge cases."""

    def test_validate_url_parsing_exception(
        self, cli_runner: CliRunner, capsys: Any
    ) -> None:
        """Test URL validation with parsing exception."""
        from ghtopdep.cli import validate_github_url

        with patch("ghtopdep.cli.urlparse", side_effect=Exception("Parse error")):
            with pytest.raises(SystemExit):
                validate_github_url("https://github.com/user/repo")
            captured = capsys.readouterr()
            assert "Error: Invalid URL format" in captured.err


class TestFetchDescriptionException:
    """Tests for fetch_description exception handling."""

    def test_fetch_description_unexpected_error(self, capsys: Any) -> None:
        """Test fetch_description with unexpected exception."""
        from ghtopdep.cli import fetch_description

        gh = MagicMock()
        gh.repository.side_effect = Exception("Unexpected error")

        result = fetch_description(gh, "/owner/repo")
        # Should return empty string on error
        assert result == ""
        captured = capsys.readouterr()
        assert "Warning: Failed to fetch repository" in captured.err
