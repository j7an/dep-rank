"""GitHub URL validation and parsing."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_VALID_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")


def validate_github_url(url: str) -> tuple[str, str]:
    """Validate a GitHub repository URL and extract owner/repo.

    Accepts:
        - https://github.com/owner/repo
        - http://www.github.com/owner/repo/
        - owner/repo (shorthand)

    Returns:
        Tuple of (owner, repository).

    Raises:
        ValueError: If the URL is not a valid GitHub repository URL.
    """
    if not url or not isinstance(url, str):
        msg = "URL cannot be empty"
        raise ValueError(msg)

    url = url.strip()

    # Handle bare owner/repo shorthand (no scheme, no domain)
    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc:
        # Treat as owner/repo
        path = url.strip("/")
    else:
        if parsed.netloc not in ("github.com", "www.github.com"):
            msg = f"URL must be a github.com repository URL, got: {parsed.netloc}"
            raise ValueError(msg)
        path = parsed.path.strip("/")

    if not path:
        msg = "Invalid GitHub URL — missing repository path. Expected format: https://github.com/owner/repository"
        raise ValueError(msg)

    segments = path.split("/")
    if len(segments) != 2:
        msg = (
            f"Expected format: https://github.com/owner/repository"
            f" — got {len(segments)} path segment(s): {path}"
        )
        raise ValueError(msg)

    owner, repo = segments

    if not owner or not repo:
        msg = "Both owner and repository names must be non-empty"
        raise ValueError(msg)

    if not _VALID_NAME.match(owner):
        msg = (
            f"Invalid owner name '{owner}' — must contain only"
            " alphanumeric characters, dots, hyphens, or underscores"
        )
        raise ValueError(msg)

    if not _VALID_NAME.match(repo):
        msg = (
            f"Invalid repository name '{repo}' — must contain only"
            " alphanumeric characters, dots, hyphens, or underscores"
        )
        raise ValueError(msg)

    return owner, repo
