"""Tests for GitHub URL validation."""

from __future__ import annotations

import pytest

from dep_rank.core.validation import validate_github_url


class TestValidateGithubUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            pytest.param("https://github.com/django/django", ("django", "django"), id="https"),
            pytest.param("http://github.com/owner/repo", ("owner", "repo"), id="http"),
            pytest.param("https://www.github.com/owner/repo", ("owner", "repo"), id="www-host"),
            pytest.param("https://github.com/owner/repo/", ("owner", "repo"), id="trailing-slash"),
            pytest.param(
                "https://github.com/my-org/my.repo_name",
                ("my-org", "my.repo_name"),
                id="dash-dot-underscore-names",
            ),
            pytest.param("django/django", ("django", "django"), id="owner-repo-shorthand"),
            pytest.param("  django/django  ", ("django", "django"), id="surrounding-whitespace"),
        ],
    )
    def test_valid_urls(self, url: str, expected: tuple[str, str]) -> None:
        assert validate_github_url(url) == expected

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            pytest.param("https://gitlab.com/owner/repo", "github.com", id="non-github-host"),
            pytest.param(
                "https://github.com/owner/repo/extra", "owner/repository", id="extra-path-segment"
            ),
            pytest.param("https://github.com/owner", "owner/repository", id="missing-repo"),
            pytest.param(
                "https://github.com/owner/repo@bad", "alphanumeric", id="invalid-character"
            ),
            pytest.param("", "URL cannot be empty", id="empty-string"),
            pytest.param("/", "missing repository path", id="slash-only"),
            pytest.param("ow ner/repo", "Invalid owner name", id="space-in-owner"),
            pytest.param("owner/re po", "Invalid repository name", id="space-in-repo"),
            pytest.param("https://github.com", "missing repository path", id="bare-host"),
        ],
    )
    def test_invalid_urls(self, url: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            validate_github_url(url)
