"""Pytest configuration and fixtures for ghtopdep tests."""

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_github_client() -> MagicMock:
    """Create a mock GitHub client."""
    gh = MagicMock()
    return gh


@pytest.fixture
def mock_requests_session() -> MagicMock:
    """Create a mock requests session."""
    session = MagicMock()
    return session


@pytest.fixture
def sample_repos() -> list[dict[str, Any]]:
    """Sample repository data for testing."""
    return [
        {"url": "https://github.com/user1/repo1", "stars": 100},
        {"url": "https://github.com/user2/repo2", "stars": 500},
        {"url": "https://github.com/user3/repo3", "stars": 250},
    ]


@pytest.fixture
def sample_repos_with_description() -> list[dict[str, Any]]:
    """Sample repository data with descriptions."""
    return [
        {
            "url": "https://github.com/user1/repo1",
            "stars": 100,
            "description": "First repo",
        },
        {
            "url": "https://github.com/user2/repo2",
            "stars": 500,
            "description": "Second repo",
        },
        {
            "url": "https://github.com/user3/repo3",
            "stars": 250,
            "description": "Third repo",
        },
    ]


@pytest.fixture
def html_response_dependents() -> str:
    """Sample HTML response from GitHub dependents page."""
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
                            <span>1,500</span>
                        </div>
                    </div>
                    <div class="flex-items-center">
                        <span>
                            <a class="text-bold" href="/user2/repo2">user2/repo2</a>
                        </span>
                        <div>
                            <span>500</span>
                        </div>
                    </div>
                    <div class="flex-items-center">
                        <span>
                            <a class="text-bold" href="/user3/repo3">user3/repo3</a>
                        </span>
                        <div>
                            <span>50</span>
                        </div>
                    </div>
                </div>
                <div class="paginate-container">
                    <a href="/network/dependents?page=2">Next</a>
                </div>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def html_response_last_page() -> str:
    """Sample HTML response from the last page of dependents."""
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
                            <a class="text-bold" href="/user4/repo4">user4/repo4</a>
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


@pytest.fixture(autouse=True)
def env_setup() -> Generator[None, None, None]:
    """Set up environment variables for tests."""
    original_env = {}

    # Store original values
    for key in ["GHTOPDEP_TOKEN", "GHTOPDEP_BASE_URL", "GHTOPDEP_ENV"]:
        original_env[key] = os.environ.get(key)

    yield

    # Restore original values
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
