"""Tests for Pydantic models."""

from __future__ import annotations

from datetime import UTC, datetime

from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
)


class TestRepository:
    def test_create_minimal(self) -> None:
        repo = Repository(
            owner="django", name="django", url="https://github.com/django/django", stars=82000
        )
        assert repo.owner == "django"
        assert repo.name == "django"
        assert repo.stars == 82000
        assert repo.description is None

    def test_create_with_description(self) -> None:
        repo = Repository(
            owner="django",
            name="django",
            url="https://github.com/django/django",
            stars=82000,
            description="The Web framework for perfectionists.",
        )
        assert repo.description == "The Web framework for perfectionists."

    def test_json_round_trip(self) -> None:
        repo = Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)
        data = repo.model_dump_json()
        restored = Repository.model_validate_json(data)
        assert restored == repo


class TestDependentType:
    def test_values(self) -> None:
        assert DependentType.REPOSITORY == "REPOSITORY"
        assert DependentType.PACKAGE == "PACKAGE"


class TestDependentsResult:
    def test_create(self) -> None:
        now = datetime.now(tz=UTC)
        result = DependentsResult(
            source="https://github.com/django/django",
            total_count=3000,
            filtered_count=150,
            repos=[
                Repository(owner="a", name="b", url="https://github.com/a/b", stars=100),
            ],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=now,
        )
        assert result.total_count == 3000
        assert len(result.repos) == 1

    def test_json_serialization(self) -> None:
        now = datetime.now(tz=UTC)
        result = DependentsResult(
            source="https://github.com/x/y",
            total_count=10,
            filtered_count=5,
            repos=[],
            dependent_type=DependentType.PACKAGE,
            scraped_at=now,
        )
        json_str = result.model_dump_json()
        assert "PACKAGE" in json_str
        restored = DependentsResult.model_validate_json(json_str)
        assert restored.dependent_type == DependentType.PACKAGE


class TestCodeSearchResult:
    def test_create(self) -> None:
        repo = Repository(owner="a", name="b", url="https://github.com/a/b", stars=50)
        hit = CodeSearchHit(
            repo=repo,
            file_url="https://github.com/a/b/blob/main/src/utils.py",
            file_path="src/utils.py",
            matches=3,
        )
        result = CodeSearchResult(
            source="https://github.com/x/y",
            query="import pandas",
            hits=[hit],
            searched_repos=5,
        )
        assert result.searched_repos == 5
        assert result.hits[0].matches == 3
