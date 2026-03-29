"""Tests for GitHub URL validation."""

from __future__ import annotations

import pytest

from dep_rank.core.validation import validate_github_url


class TestValidateGithubUrl:
    def test_valid_https(self) -> None:
        assert validate_github_url("https://github.com/django/django") == ("django", "django")

    def test_valid_http(self) -> None:
        assert validate_github_url("http://github.com/owner/repo") == ("owner", "repo")

    def test_valid_www(self) -> None:
        assert validate_github_url("https://www.github.com/owner/repo") == ("owner", "repo")

    def test_valid_trailing_slash(self) -> None:
        assert validate_github_url("https://github.com/owner/repo/") == ("owner", "repo")

    def test_valid_hyphen_dot_underscore(self) -> None:
        assert validate_github_url("https://github.com/my-org/my.repo_name") == (
            "my-org",
            "my.repo_name",
        )

    def test_invalid_not_github(self) -> None:
        with pytest.raises(ValueError, match="github.com"):
            validate_github_url("https://gitlab.com/owner/repo")

    def test_invalid_too_many_segments(self) -> None:
        with pytest.raises(ValueError, match="owner/repository"):
            validate_github_url("https://github.com/owner/repo/extra")

    def test_invalid_too_few_segments(self) -> None:
        with pytest.raises(ValueError, match="owner/repository"):
            validate_github_url("https://github.com/owner")

    def test_invalid_empty_owner(self) -> None:
        with pytest.raises(ValueError):
            validate_github_url("https://github.com//repo")

    def test_invalid_special_chars(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            validate_github_url("https://github.com/owner/repo@bad")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            validate_github_url("")

    def test_bare_owner_repo(self) -> None:
        """Accept owner/repo shorthand without full URL."""
        assert validate_github_url("django/django") == ("django", "django")

    def test_bare_missing_path(self) -> None:
        """Bare input with no path raises ValueError."""
        with pytest.raises(ValueError, match="missing repository path"):
            validate_github_url("/")

    def test_bare_empty_owner_and_repo(self) -> None:
        """Bare input like '/' with empty segments raises ValueError."""
        with pytest.raises(ValueError):
            validate_github_url("//")

    def test_invalid_owner_chars_bare(self) -> None:
        """Bare owner/repo with invalid owner characters."""
        with pytest.raises(ValueError, match="Invalid owner name"):
            validate_github_url("ow ner/repo")

    def test_invalid_repo_chars_bare(self) -> None:
        """Bare owner/repo with invalid repo characters."""
        with pytest.raises(ValueError, match="Invalid repository name"):
            validate_github_url("owner/re po")

    def test_github_url_missing_path(self) -> None:
        """GitHub URL with no path."""
        with pytest.raises(ValueError, match="missing repository path"):
            validate_github_url("https://github.com")

    def test_empty_owner_empty_repo(self) -> None:
        """Both owner and repo empty."""
        with pytest.raises(ValueError):
            validate_github_url("https://github.com//")

    def test_whitespace_url(self) -> None:
        """Whitespace-padded URL is trimmed and handled."""
        assert validate_github_url("  django/django  ") == ("django", "django")
