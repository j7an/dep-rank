"""Tests for GitHub URL validation."""

from __future__ import annotations

import pytest

from dep_rank.core.validation import validate_github_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/django/django", ("django", "django")),
        ("http://github.com/owner/repo", ("owner", "repo")),
        ("https://www.github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/my-org/my.repo_name", ("my-org", "my.repo_name")),
        ("django/django", ("django", "django")),  # owner/repo shorthand
        ("  django/django  ", ("django", "django")),  # surrounding whitespace is trimmed
    ],
)
def test_valid_urls(url: str, expected: tuple[str, str]) -> None:
    assert validate_github_url(url) == expected


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://gitlab.com/owner/repo", "github.com"),
        ("https://github.com/owner/repo/extra", "owner/repository"),
        ("https://github.com/owner", "owner/repository"),
        ("https://github.com/owner/repo@bad", "alphanumeric"),
        ("", "URL cannot be empty"),
        ("/", "missing repository path"),
        ("ow ner/repo", "Invalid owner name"),
        ("owner/re po", "Invalid repository name"),
        ("https://github.com", "missing repository path"),
    ],
)
def test_invalid_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_github_url(url)
