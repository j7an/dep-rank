"""Integration tests for error handling in ghtopdep API calls."""

from typing import Any
from unittest.mock import MagicMock, patch

import requests
from click.testing import CliRunner

from ghtopdep.cli import cli


class TestEndToEndErrorHandling:
    """Integration tests for end-to-end error scenarios."""

    def test_cli_with_network_failure_on_scraping(self) -> None:
        """Test CLI handles network failure gracefully during scraping."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            # Network fails on first scraping request
            mock_session.get.side_effect = requests.exceptions.ConnectionError(
                "Network down"
            )

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle gracefully and exit with 0
            assert result.exit_code == 0

    def test_cli_with_multiple_error_types(self) -> None:
        """Test CLI handles various error types in sequence."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 100

            # Simulate multiple failures in sequence
            mock_session.get.side_effect = [
                requests.exceptions.Timeout(),  # First timeout
                requests.exceptions.ConnectionError(
                    "Connection reset"
                ),  # Then connection error
            ]

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle all gracefully
            assert result.exit_code == 0

    def test_cli_invalid_url_handling(self) -> None:
        """Test CLI rejects invalid URLs."""
        runner = CliRunner()

        result = runner.invoke(cli, ["invalid-url"])

        # Should exit with error code
        assert result.exit_code == 1
        assert "Error" in result.output or "Invalid" in result.output

    def test_cli_report_mode_missing_base_url(self) -> None:
        """Test CLI requires BASE_URL for report mode."""
        runner = CliRunner()

        result = runner.invoke(cli, ["https://github.com/test/repo", "--report"])

        # Should exit because BASE_URL is required
        assert result.exit_code == 1
        assert "BASE_URL" in result.output or "Error" in result.output


class TestAPIFailureRecovery:
    """Tests for API failure recovery mechanisms."""

    def test_scraping_continues_after_parse_error(self) -> None:
        """Test scraping continues after HTML parse error."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
            patch("ghtopdep.cli.HTMLParser") as mock_parser_class,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            # First parse fails, should catch and continue
            mock_parser_class.side_effect = Exception("Parse error")

            result = runner.invoke(cli, ["https://github.com/test/repo"])

            # Should handle gracefully without crashing
            assert "Warning" in result.output or result.exit_code == 0

    def test_search_continues_after_api_error(self) -> None:
        """Test search continues after API error on one repository."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
            patch("ghtopdep.cli.HTMLParser") as mock_parser_class,
            patch("ghtopdep.cli.github3.login") as mock_login,
            patch("ghtopdep.cli.CacheControl"),
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 0

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            mock_parser = MagicMock()
            mock_parser.css.return_value = []
            mock_parser_class.return_value = mock_parser

            mock_gh = MagicMock()
            mock_login.return_value = mock_gh
            mock_gh.search_code.side_effect = Exception("Rate limit exceeded")

            result = runner.invoke(
                cli,
                [
                    "https://github.com/test/repo",
                    "--search",
                    "test",
                    "--token",
                    "test-token",
                ],
            )

            # Should handle gracefully
            assert result.exit_code == 0


class TestTimeoutHandling:
    """Tests for timeout handling across different scenarios."""

    def test_get_max_deps_respects_timeout(self) -> None:
        """Test that get_max_deps respects timeout configuration."""
        from ghtopdep.cli import REQUEST_TIMEOUT, get_max_deps

        sess = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.text = "<html></html>"
        sess.get.return_value = response

        with patch("ghtopdep.cli.HTMLParser") as mock_parser:
            mock_element = MagicMock()
            mock_element.text.return_value = "100 Repositories"
            mock_parser_instance = MagicMock()
            mock_parser_instance.css_first.return_value = mock_element
            mock_parser.return_value = mock_parser_instance

            get_max_deps(sess, "http://github.com/test/repo/network/dependents")

            # Verify timeout was passed to requests.get
            sess.get.assert_called_once()
            call_kwargs = sess.get.call_args[1]
            assert call_kwargs.get("timeout") == REQUEST_TIMEOUT

    def test_scraping_loop_respects_timeout(self) -> None:
        """Test that scraping loop respects timeout configuration."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            from ghtopdep.cli import REQUEST_TIMEOUT

            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            with patch("ghtopdep.cli.HTMLParser") as mock_parser:
                mock_parser_instance = MagicMock()
                mock_parser_instance.css.return_value = []
                mock_parser.return_value = mock_parser_instance

                runner.invoke(cli, ["https://github.com/test/repo"])

                # Check that timeout was passed
                if mock_session.get.call_count > 0:
                    # At least one call should include timeout
                    calls = mock_session.get.call_args_list
                    has_timeout = any(
                        call[1].get("timeout") == REQUEST_TIMEOUT for call in calls
                    )
                    assert has_timeout


class TestPaginationErrorHandling:
    """Tests for pagination-related error handling."""

    def test_pagination_stops_at_max_pages(self) -> None:
        """Test that pagination stops at MAX_PAGES limit."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            from ghtopdep.cli import MAX_PAGES

            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = MAX_PAGES * 100  # Very large number

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            with patch("ghtopdep.cli.HTMLParser") as mock_parser:
                mock_parser_instance = MagicMock()
                mock_next = MagicMock()
                mock_next.attributes = {
                    "href": "https://github.com/test/repo/network/dependents?page=2"
                }
                mock_parser_instance.css.return_value = [mock_next, mock_next]
                mock_parser_instance.css_first.return_value = None
                mock_parser.return_value = mock_parser_instance

                runner.invoke(cli, ["https://github.com/test/repo"])

                # Should complete even though pagination could continue infinitely
                # Number of get calls should be approximately MAX_PAGES
                assert (
                    mock_session.get.call_count <= MAX_PAGES + 10
                )  # Allow small buffer

    def test_pagination_handles_missing_href(self) -> None:
        """Test pagination handles missing href attribute."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            with patch("ghtopdep.cli.HTMLParser") as mock_parser:
                mock_parser_instance = MagicMock()
                mock_button = MagicMock()
                mock_button.attributes = {}  # No href attribute
                mock_parser_instance.css.return_value = [mock_button]
                mock_parser_instance.css_first.return_value = None
                mock_parser.return_value = mock_parser_instance

                result = runner.invoke(cli, ["https://github.com/test/repo"])

                # Should handle missing href gracefully
                assert result.exit_code == 0


class TestDataValidationErrorHandling:
    """Tests for data validation error handling."""

    def test_invalid_star_count_is_skipped(self) -> None:
        """Test that items with invalid star counts are skipped."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            with patch("ghtopdep.cli.HTMLParser") as mock_parser:
                mock_parser_instance = MagicMock()

                # Mock dependent with invalid stars
                mock_dep = MagicMock()
                mock_star_elem = MagicMock()
                mock_star_elem.text.return_value = "not-a-number"
                mock_dep.css.side_effect = (
                    lambda sel: [mock_star_elem] if "STARS" in sel else []
                )

                mock_parser_instance.css.side_effect = (
                    lambda sel: [mock_dep] if "ITEM" in sel else []
                )
                mock_parser_instance.css_first.return_value = None
                mock_parser.return_value = mock_parser_instance

                result = runner.invoke(cli, ["https://github.com/test/repo"])

                # Should skip invalid item and continue
                assert result.exit_code == 0

    def test_missing_url_attribute_is_handled(self) -> None:
        """Test that missing URL attributes are handled gracefully."""
        runner = CliRunner()

        with (
            patch("ghtopdep.cli.requests.session") as mock_session_class,
            patch("ghtopdep.cli.get_max_deps") as mock_max_deps,
        ):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_max_deps.return_value = 30

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"
            mock_response.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_response

            with patch("ghtopdep.cli.HTMLParser") as mock_parser:
                mock_parser_instance = MagicMock()

                # Mock dependent with missing URL
                mock_dep = MagicMock()
                mock_star_elem = MagicMock()
                mock_star_elem.text.return_value = "100"

                def css_side_effect(sel: Any) -> list[Any]:
                    if "STARS" in sel:
                        return [mock_star_elem]
                    elif "REPO" in sel:
                        # Return element without href attribute
                        return [MagicMock(attributes={})]
                    return []

                mock_dep.css.side_effect = css_side_effect

                mock_parser_instance.css.side_effect = (
                    lambda sel: [mock_dep] if "ITEM" in sel else []
                )
                mock_parser_instance.css_first.return_value = None
                mock_parser.return_value = mock_parser_instance

                result = runner.invoke(cli, ["https://github.com/test/repo"])

                # Should skip item with missing URL and continue
                assert result.exit_code == 0
