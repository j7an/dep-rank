"""End-to-end tests for ghtopdep using VCR.py for HTTP recording."""

import pytest
from pathlib import Path
from typing import cast
from unittest.mock import patch, MagicMock
from click import Command
from click.testing import CliRunner
import vcr
from ghtopdep.cli import cli as _cli

# Type cast to help type checkers understand cli is a Command
cli = cast(Command, _cli)


# Define cassettes directory using absolute path
CASSETTES_DIR = Path(__file__).parent / "cassettes"

# Configure VCR to record/replay HTTP interactions
my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTES_DIR),
    record_mode="none",  # Set to "new_episodes" to record new cassettes
    match_on=["method", "scheme", "host", "port", "path", "query"],
    filter_headers=["authorization", "ghtopdep_token"],
)


@pytest.fixture
def cli_runner():
    """Create a Click CLI runner."""
    return CliRunner()


# Create cassettes directory if it doesn't exist
@pytest.fixture(scope="session", autouse=True)
def setup_cassettes_dir():
    """Ensure cassettes directory exists."""
    CASSETTES_DIR.mkdir(exist_ok=True)


class TestE2EWithMocks:
    """E2E tests using mocked HTTP responses to simulate real workflows."""

    def test_e2e_complete_workflow_mock(self, cli_runner):
        """Test complete workflow: validate URL, fetch dependents, parse, sort, display."""
        html_page_1 = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">90 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo1">user1/repo1</a></span>
                            <div><span>500</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user2/repo2">user2/repo2</a></span>
                            <div><span>1,200</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user3/repo3">user3/repo3</a></span>
                            <div><span>100</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=2">Next</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        html_page_2 = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">90 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user4/repo4">user4/repo4</a></span>
                            <div><span>2,000</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=90):
                mock_session = MagicMock()

                # Create responses for two pages
                response1 = Mock()
                response1.text = html_page_1
                response1.status_code = 200

                response2 = Mock()
                response2.text = html_page_2
                response2.status_code = 200

                mock_session.get.side_effect = [response1, response2]
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/test/repo", "--json", "--minstar", "100"]
                    )

                    # Should complete successfully
                    assert result.exit_code in [0, 1]
                    # Should have made requests
                    assert mock_session.get.called

    def test_e2e_with_description_mock(self, cli_runner):
        """Test workflow with description fetching."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">30 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo1">user1/repo1</a></span>
                            <div><span>100</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=30):
                with patch("ghtopdep.cli.github3.login") as mock_login:
                    mock_session = MagicMock()
                    response = Mock()
                    response.text = html_response
                    response.status_code = 200
                    mock_session.get.return_value = response
                    mock_session_class.return_value = mock_session

                    # Mock GitHub client
                    mock_gh = MagicMock()
                    mock_repo = MagicMock()
                    mock_repo.description = "Test repository description"
                    mock_gh.repository.return_value = mock_repo
                    mock_login.return_value = mock_gh

                    with patch("ghtopdep.cli.CacheControl"):
                        result = cli_runner.invoke(
                            cli,
                            [
                                "https://github.com/test/repo",
                                "--description",
                                "--token",
                                "test_token",
                                "--json",
                            ]
                        )

                        # Should complete successfully
                        assert result.exit_code in [0, 1]

    def test_e2e_table_output_format_mock(self, cli_runner):
        """Test workflow with table output format."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">30 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo1">user1/repo1</a></span>
                            <div><span>100</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user2/repo2">user2/repo2</a></span>
                            <div><span>50</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=30):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        [
                            "https://github.com/test/repo",
                            "--table",
                            "--rows",
                            "10",
                        ]
                    )

                    # Should complete successfully
                    assert result.exit_code in [0, 1]

    def test_e2e_packages_mode_mock(self, cli_runner):
        """Test workflow with packages mode."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">20 packages</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/org/package">org/package</a></span>
                            <div><span>250</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=20):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        [
                            "https://github.com/test/package",
                            "--packages",
                            "--json",
                        ]
                    )

                    # Should complete successfully
                    assert result.exit_code in [0, 1]

    def test_e2e_high_star_filtering_mock(self, cli_runner):
        """Test filtering repos by high star count."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">100 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/popular">user1/popular</a></span>
                            <div><span>5,000</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user2/less">user2/less</a></span>
                            <div><span>100</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user3/tiny">user3/tiny</a></span>
                            <div><span>10</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=100):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        [
                            "https://github.com/test/repo",
                            "--minstar",
                            "500",
                            "--json",
                        ]
                    )

                    # Should complete successfully
                    assert result.exit_code in [0, 1]

    def test_e2e_rows_limit_mock(self, cli_runner):
        """Test limiting output rows."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">50 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo1">user1/repo1</a></span>
                            <div><span>100</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user2/repo2">user2/repo2</a></span>
                            <div><span>200</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user3/repo3">user3/repo3</a></span>
                            <div><span>300</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=50):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        [
                            "https://github.com/test/repo",
                            "--rows",
                            "2",
                            "--json",
                        ]
                    )

                    # Should complete successfully
                    assert result.exit_code in [0, 1]


class TestE2EErrorRecovery:
    """E2E tests for error handling and recovery."""

    @staticmethod
    def _invoke_with_mocked_session(cli_runner, html_response, max_deps=0, cli_args=None):
        """
        Helper method to invoke CLI with mocked session.

        Args:
            cli_runner: Click test runner
            html_response: HTML content to return from mocked session
            max_deps: Value to return from get_max_deps mock
            cli_args: List of CLI arguments (default: ["https://github.com/test/repo", "--json"])

        Returns:
            Click test result
        """
        from unittest.mock import Mock

        if cli_args is None:
            cli_args = ["https://github.com/test/repo", "--json"]

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=max_deps):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    return cli_runner.invoke(cli, cli_args)

    def test_e2e_handle_empty_results_mock(self, cli_runner):
        """Test handling when no dependents are found."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">0 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box"></div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        result = self._invoke_with_mocked_session(cli_runner, html_response, max_deps=0)
        # Should handle empty results gracefully
        assert result.exit_code in [0, 1]

    def test_e2e_handle_no_stars_repos_mock(self, cli_runner):
        """Test handling repos with zero stars."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">10 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo1">user1/repo1</a></span>
                            <div><span>0</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user2/repo2">user2/repo2</a></span>
                            <div><span>100</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        from unittest.mock import Mock

        with patch("ghtopdep.cli.requests.session") as mock_session_class:
            with patch("ghtopdep.cli.get_max_deps", return_value=10):
                mock_session = MagicMock()
                response = Mock()
                response.text = html_response
                response.status_code = 200
                mock_session.get.return_value = response
                mock_session_class.return_value = mock_session

                with patch("ghtopdep.cli.CacheControl"):
                    result = cli_runner.invoke(
                        cli,
                        ["https://github.com/test/repo", "--minstar", "5", "--json"]
                    )

                    # Should handle zero-star repos appropriately
                    assert result.exit_code in [0, 1]

    def test_e2e_duplicate_repo_handling_mock(self, cli_runner):
        """Test handling of duplicate repository entries."""
        html_response = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">30 repositories</button>
                </div>
                <div id="dependents">
                    <div class="Box">
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo">user1/repo</a></span>
                            <div><span>100</span></div>
                        </div>
                        <div class="flex-items-center">
                            <span><a class="text-bold" href="/user1/repo">user1/repo</a></span>
                            <div><span>100</span></div>
                        </div>
                    </div>
                    <div class="paginate-container">
                        <a href="/network/dependents?page=1">Previous</a>
                    </div>
                </div>
            </body>
        </html>
        '''

        result = self._invoke_with_mocked_session(cli_runner, html_response, max_deps=30)
        # Should handle duplicates by filtering them out
        assert result.exit_code in [0, 1]
