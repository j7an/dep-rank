"""Tests for GitHub code search."""

from __future__ import annotations

import httpx2

from dep_rank.core.search import search_code
from tests.conftest import FakeHTTP, make_repo


class TestSearchCode:
    async def test_basic_search(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        repos = [make_repo("alpha", "framework", stars=5000)]
        search_response = {
            "total_count": 2,
            "items": [
                {
                    "html_url": "https://github.com/alpha/framework/blob/main/src/app.py",
                    "path": "src/app.py",
                    "text_matches": [{"fragment": "import pandas"}],
                },
                {
                    "html_url": "https://github.com/alpha/framework/blob/main/tests/test.py",
                    "path": "tests/test.py",
                    "text_matches": [{"fragment": "import pandas"}, {"fragment": "import pandas"}],
                },
            ],
        }
        mock_http.get(
            "https://api.github.com/search/code?q=import%20pandas%20repo%3Aalpha%2Fframework",
            payload=search_response,
        )
        result = await search_code(session, repos, "import pandas", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 2
        assert result.hits[0].file_path == "src/app.py"

    async def test_respects_max_repos(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(5)]
        for i in range(2):
            mock_http.get(
                f"https://api.github.com/search/code?q=test%20repo%3Ao{i}%2Fr{i}",
                payload={"total_count": 0, "items": []},
            )
        result = await search_code(session, repos, "test", token="fake", max_repos=2)
        assert result.searched_repos == 2

    async def test_empty_repos_list(self, session: httpx2.AsyncClient) -> None:
        result = await search_code(session, [], "query", token="fake")
        assert result.searched_repos == 0
        assert len(result.hits) == 0

    async def test_non_200_status_skipped(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """Non-200 responses are silently skipped."""
        repos = [make_repo("alpha", "framework")]
        mock_http.get(
            "https://api.github.com/search/code?q=import%20os%20repo%3Aalpha%2Fframework",
            status=403,
        )
        result = await search_code(session, repos, "import os", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 0

    async def test_client_error_skipped(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        """RequestError exceptions are silently skipped."""
        repos = [make_repo("alpha", "framework")]
        mock_http.get(
            "https://api.github.com/search/code?q=import%20os%20repo%3Aalpha%2Fframework",
            exception=httpx2.ConnectError("connection failed"),
        )
        result = await search_code(session, repos, "import os", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 0
