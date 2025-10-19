"""Tests for HTML parsing and related functions."""

import calendar
import datetime
from unittest.mock import Mock, MagicMock
from email.utils import formatdate, parsedate
from ghtopdep.cli import get_max_deps, fetch_description, OneDayHeuristic, show_result


class TestGetMaxDeps:
    """Tests for the get_max_deps function."""

    def test_get_max_deps_single_digit(self, html_response_dependents):
        """Test extracting max dependencies from HTML."""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.text = html_response_dependents
        mock_session.get.return_value = mock_response

        result = get_max_deps(mock_session, "https://github.com/test/repo/network/dependents")
        assert result == 30

    def test_get_max_deps_large_number(self):
        """Test with large number of dependencies."""
        html = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">10,000 repositories</button>
                </div>
            </body>
        </html>
        '''
        mock_session = Mock()
        mock_response = Mock()
        mock_response.text = html
        mock_session.get.return_value = mock_response

        result = get_max_deps(mock_session, "https://github.com/test/repo/network/dependents")
        assert result == 10000

    def test_get_max_deps_calls_session_get(self):
        """Test that get_max_deps calls session.get with correct URL."""
        html = '''
        <html>
            <body>
                <div class="table-list-header-toggle">
                    <button class="btn-link selected">100 repositories</button>
                </div>
            </body>
        </html>
        '''
        mock_session = Mock()
        mock_response = Mock()
        mock_response.text = html
        mock_session.get.return_value = mock_response

        url = "https://github.com/test/repo/network/dependents"
        get_max_deps(mock_session, url)
        mock_session.get.assert_called_once_with(url, timeout=30)


class TestFetchDescription:
    """Tests for the fetch_description function."""

    def test_fetch_description_success(self):
        """Test successful description fetch."""
        gh = MagicMock()
        repo = MagicMock()
        repo.description = "This is a test repository"
        gh.repository.return_value = repo

        result = fetch_description(gh, "/owner/repo")
        assert "test repository" in result

    def test_fetch_description_empty_description(self):
        """Test when repository has no description."""
        gh = MagicMock()
        repo = MagicMock()
        repo.description = None
        gh.repository.return_value = repo

        result = fetch_description(gh, "/owner/repo")
        assert result == " "

    def test_fetch_description_long_description(self):
        """Test that long descriptions are truncated."""
        gh = MagicMock()
        repo = MagicMock()
        repo.description = "a" * 100
        gh.repository.return_value = repo

        result = fetch_description(gh, "/owner/repo")
        # textwrap.shorten should truncate to 60 chars
        assert len(result) <= 63  # 60 + "..."

    def test_fetch_description_parses_url_correctly(self):
        """Test that URL is parsed correctly to extract owner and repo."""
        gh = MagicMock()
        repo = MagicMock()
        repo.description = "Test"
        gh.repository.return_value = repo

        fetch_description(gh, "/myowner/myrepo")
        gh.repository.assert_called_once_with("myowner", "myrepo")


class TestOneDayHeuristic:
    """Tests for the OneDayHeuristic cache control class."""

    @staticmethod
    def _create_response_with_date(status=200, date_tuple=(2024, 1, 1, 12, 0, 0)):
        """
        Helper method to create a mock response with date header.

        Args:
            status: HTTP status code (default: 200)
            date_tuple: Date as tuple (year, month, day, hour, min, sec)

        Returns:
            Mock response object with status and date header
        """
        response = Mock()
        response.status = status
        response.headers = {"date": formatdate(calendar.timegm(datetime.datetime(*date_tuple).timetuple()))}
        return response

    def test_one_day_heuristic_cacheable_status(self):
        """Test that cacheable statuses are handled correctly."""
        heuristic = OneDayHeuristic()
        response = self._create_response_with_date(status=200)

        result = heuristic.update_headers(response)
        assert "expires" in result
        assert "cache-control" in result
        assert result["cache-control"] == "public"

    def test_one_day_heuristic_non_cacheable_status(self):
        """Test that non-cacheable statuses are ignored."""
        heuristic = OneDayHeuristic()

        response = Mock()
        response.status = 500
        response.headers = {"date": "Mon, 01 Jan 2024 12:00:00 GMT"}

        result = heuristic.update_headers(response)
        assert result == {}

    def test_one_day_heuristic_cacheable_by_default_statuses(self):
        """Test all cacheable by default statuses."""
        heuristic = OneDayHeuristic()
        cacheable_statuses = {200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501}

        for status in cacheable_statuses:
            response = self._create_response_with_date(status=status)

            result = heuristic.update_headers(response)
            assert "expires" in result
            assert "cache-control" in result

    def test_one_day_heuristic_sets_expiry_one_day_ahead(self):
        """Test that expiry is set to one day ahead."""
        heuristic = OneDayHeuristic()

        # Create a specific date
        test_date = datetime.datetime(2024, 1, 1, 12, 0, 0)
        date_str = formatdate(calendar.timegm(test_date.timetuple()))

        response = Mock()
        response.status = 200
        response.headers = {"date": date_str}

        result = heuristic.update_headers(response)

        # Parse the expiry date
        expiry_tuple = parsedate(result["expires"])
        expiry_date = datetime.datetime(*expiry_tuple[:6])

        # Should be exactly 1 day later
        expected_date = test_date + datetime.timedelta(days=1)
        assert expiry_date == expected_date

    def test_one_day_heuristic_warning_message(self):
        """Test that warning message is returned."""
        heuristic = OneDayHeuristic()

        response = Mock()
        warning = heuristic.warning(response)
        assert "Stale" in warning
        assert "110 - " in warning


class TestShowResult:
    """Tests for the show_result function."""

    def test_show_result_table_format(self, capsys):
        """Test output in table format."""
        repos = [
            {"url": "https://github.com/user/repo1", "stars": 100},
            {"url": "https://github.com/user/repo2", "stars": 50},
        ]

        show_result(repos, total_repos_count=2, more_than_zero_count=2, destinations="repositories", table=True)

        captured = capsys.readouterr()
        assert "https://github.com/user/repo1" in captured.out
        assert "https://github.com/user/repo2" in captured.out
        assert "found 2 repositories" in captured.out

    def test_show_result_json_format(self, capsys):
        """Test output in JSON format."""
        repos = [
            {"url": "https://github.com/user/repo1", "stars": 100},
        ]

        show_result(repos, total_repos_count=1, more_than_zero_count=1, destinations="repositories", table=False)

        captured = capsys.readouterr()
        assert "https://github.com/user/repo1" in captured.out
        assert "100" in captured.out

    def test_show_result_empty_list_table(self, capsys):
        """Test table output with empty results."""
        show_result([], total_repos_count=0, more_than_zero_count=0, destinations="repositories", table=True)

        captured = capsys.readouterr()
        assert "Doesn't find any repositories" in captured.out

    def test_show_result_empty_list_json(self, capsys):
        """Test JSON output with empty results."""
        show_result([], total_repos_count=0, more_than_zero_count=0, destinations="repositories", table=False)

        captured = capsys.readouterr()
        assert "[]" in captured.out

    def test_show_result_packages_singular(self, capsys):
        """Test output with 'packages' destination."""
        repos = [{"url": "https://github.com/user/pkg", "stars": 50}]

        show_result(repos, total_repos_count=1, more_than_zero_count=1, destinations="packages", table=True)

        captured = capsys.readouterr()
        assert "found 1 packages" in captured.out

    def test_show_result_with_description(self, capsys):
        """Test output with repository descriptions."""
        repos = [
            {"url": "https://github.com/user/repo1", "stars": 100, "description": "Test repo"},
        ]

        show_result(repos, total_repos_count=1, more_than_zero_count=1, destinations="repositories", table=True)

        captured = capsys.readouterr()
        assert "Test repo" in captured.out
