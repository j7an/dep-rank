"""Tests for GitHub code search."""

from __future__ import annotations

import aiohttp
from aiohttp import ClientSession
from aioresponses import aioresponses

from dep_rank.core.search import search_code
from tests.conftest import make_repo


class TestSearchCode:
    async def test_basic_search(self) -> None:
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
        with aioresponses() as m:
            m.get(
                "https://api.github.com/search/code?q=import%20pandas%20repo%3Aalpha%2Fframework",
                payload=search_response,
            )
            async with ClientSession() as session:
                result = await search_code(session, repos, "import pandas", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 2
        assert result.hits[0].file_path == "src/app.py"

    async def test_respects_max_repos(self) -> None:
        repos = [make_repo(f"o{i}", f"r{i}") for i in range(5)]
        with aioresponses() as m:
            for i in range(2):
                m.get(
                    f"https://api.github.com/search/code?q=test%20repo%3Ao{i}%2Fr{i}",
                    payload={"total_count": 0, "items": []},
                )
            async with ClientSession() as session:
                result = await search_code(session, repos, "test", token="fake", max_repos=2)
        assert result.searched_repos == 2

    async def test_empty_repos_list(self) -> None:
        async with ClientSession() as session:
            result = await search_code(session, [], "query", token="fake")
        assert result.searched_repos == 0
        assert len(result.hits) == 0

    async def test_non_200_status_skipped(self) -> None:
        """Non-200 responses are silently skipped."""
        repos = [make_repo("alpha", "framework")]
        with aioresponses() as m:
            m.get(
                "https://api.github.com/search/code?q=import%20os%20repo%3Aalpha%2Fframework",
                status=403,
            )
            async with ClientSession() as session:
                result = await search_code(session, repos, "import os", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 0

    async def test_client_error_skipped(self) -> None:
        """ClientError exceptions are silently skipped."""
        repos = [make_repo("alpha", "framework")]
        with aioresponses() as m:
            m.get(
                "https://api.github.com/search/code?q=import%20os%20repo%3Aalpha%2Fframework",
                exception=aiohttp.ClientError("connection failed"),
            )
            async with ClientSession() as session:
                result = await search_code(session, repos, "import os", token="fake")
        assert result.searched_repos == 1
        assert len(result.hits) == 0
