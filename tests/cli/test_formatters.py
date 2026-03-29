"""Tests for CLI output formatters."""

from __future__ import annotations

from datetime import UTC, datetime

from dep_rank.cli.formatters import (
    humanize,
    make_progress_callback,
    print_dependents_table,
    print_search_results,
)
from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
)


class TestHumanize:
    def test_under_1000(self) -> None:
        assert humanize(999) == "999"
        assert humanize(0) == "0"

    def test_thousands(self) -> None:
        assert humanize(1500) == "1.5K"
        assert humanize(9900) == "9.9K"

    def test_ten_thousands(self) -> None:
        assert humanize(10000) == "10K"
        assert humanize(82400) == "82K"
        assert humanize(999999) == "999K"

    def test_millions(self) -> None:
        assert humanize(1000000) == "1.0M"
        assert humanize(1500000) == "1.5M"
        assert humanize(12345678) == "12M"


class TestPrintDependentsTable:
    def test_basic_table(self) -> None:
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=12500,
                ),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        # Should not raise
        print_dependents_table(result)

    def test_table_with_descriptions(self) -> None:
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=100,
            filtered_count=50,
            repos=[
                Repository(
                    owner="alpha",
                    name="framework",
                    url="https://github.com/alpha/framework",
                    stars=12500,
                    description="A web framework",
                ),
                Repository(
                    owner="beta",
                    name="toolkit",
                    url="https://github.com/beta/toolkit",
                    stars=3200,
                    description=None,
                ),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        # Should not raise — exercises the has_descriptions branch
        print_dependents_table(result)


class TestPrintSearchResults:
    def test_no_hits(self) -> None:
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[],
            searched_repos=5,
        )
        # Should not raise — exercises the "no results" branch
        print_search_results(result)

    def test_with_hits(self) -> None:
        repo = Repository(
            owner="alpha",
            name="framework",
            url="https://github.com/alpha/framework",
            stars=5000,
        )
        result = CodeSearchResult(
            source="https://github.com/django/django",
            query="import os",
            hits=[
                CodeSearchHit(
                    repo=repo,
                    file_url="https://github.com/alpha/framework/blob/main/app.py",
                    file_path="app.py",
                    matches=3,
                ),
            ],
            searched_repos=1,
        )
        # Should not raise — exercises the table building with hits
        print_search_results(result)


class TestMakeProgressCallback:
    async def test_callback_with_total(self) -> None:
        progress, callback = make_progress_callback()
        # Call with a positive total
        await callback(5, 10)

    async def test_callback_with_zero_total(self) -> None:
        progress, callback = make_progress_callback()
        # Call with zero total — exercises the else branch
        await callback(1, 0)
