"""Tests for Pydantic models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
    ScrapeReason,
    ScrapeResult,
    TrustComponents,
    TrustMetadataResult,
    TrustScore,
    TrustSignals,
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


class TestScrapeResult:
    def test_create(self) -> None:
        repo = Repository(owner="a", name="b", url="https://github.com/a/b", stars=10)
        result = ScrapeResult(
            repos=[repo],
            pages_scraped=42,
            max_pages=1000,
            estimated_total_pages=76515,
            estimated_total_dependents=2295450,
        )
        assert result.pages_scraped == 42
        assert result.max_pages == 1000
        assert result.estimated_total_pages == 76515
        assert result.estimated_total_dependents == 2295450
        assert len(result.repos) == 1

    def test_json_round_trip(self) -> None:
        result = ScrapeResult(
            repos=[],
            pages_scraped=0,
            max_pages=1000,
            estimated_total_pages=0,
            estimated_total_dependents=0,
        )
        data = result.model_dump_json()
        restored = ScrapeResult.model_validate_json(data)
        assert restored == result


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


class TestScrapeReason:
    def test_values(self) -> None:
        assert ScrapeReason.MAX_PAGES_REACHED.value == "max_pages_reached"
        assert ScrapeReason.TREND_CONVERGED.value == "trend_converged"
        assert ScrapeReason.NETWORK_FAILURE.value == "network_failure"
        assert ScrapeReason.RATE_LIMITED.value == "rate_limited"


class TestScrapeResultContractFields:
    def test_defaults_are_complete(self) -> None:
        result = ScrapeResult(
            repos=[],
            pages_scraped=0,
            max_pages=200,
            estimated_total_pages=0,
            estimated_total_dependents=0,
        )
        assert result.complete is True
        assert result.reason is None
        assert result.matched_count == 0

    def test_incomplete_with_reason(self) -> None:
        result = ScrapeResult(
            repos=[],
            pages_scraped=200,
            max_pages=200,
            estimated_total_pages=500,
            estimated_total_dependents=15000,
            complete=False,
            reason=ScrapeReason.MAX_PAGES_REACHED,
            matched_count=4200,
        )
        assert result.complete is False
        assert result.reason == "max_pages_reached"
        assert result.matched_count == 4200

    def test_complete_must_equal_reason_absence(self) -> None:
        """The terminal contract enforces ``complete == (reason is None)``."""
        # complete=True but a reason is set -> invalid.
        with pytest.raises(ValidationError):
            ScrapeResult(
                repos=[],
                pages_scraped=1,
                max_pages=200,
                estimated_total_pages=0,
                estimated_total_dependents=0,
                complete=True,
                reason=ScrapeReason.RATE_LIMITED,
            )
        # complete=False but no reason -> invalid.
        with pytest.raises(ValidationError):
            ScrapeResult(
                repos=[],
                pages_scraped=1,
                max_pages=200,
                estimated_total_pages=0,
                estimated_total_dependents=0,
                complete=False,
                reason=None,
            )

    @pytest.mark.parametrize(
        "reason",
        [
            ScrapeReason.MAX_PAGES_REACHED,
            ScrapeReason.TREND_CONVERGED,
            ScrapeReason.NETWORK_FAILURE,
            ScrapeReason.RATE_LIMITED,
        ],
    )
    def test_every_reason_marks_incomplete(self, reason: ScrapeReason) -> None:
        """All four terminal reasons construct cleanly and satisfy the invariant
        (issue #74 acceptance: every documented stop reason is representable)."""
        result = ScrapeResult(
            repos=[],
            pages_scraped=1,
            max_pages=200,
            estimated_total_pages=0,
            estimated_total_dependents=0,
            complete=False,
            reason=reason,
            matched_count=0,
        )
        assert result.complete is False
        assert result.reason == reason


class TestDependentsResultContractFields:
    def test_defaults(self) -> None:
        from datetime import UTC, datetime

        result = DependentsResult(
            source="https://github.com/x/y",
            total_count=10,
            filtered_count=10,
            repos=[],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        assert result.complete is True
        assert result.reason is None
        assert result.pages_scraped == 0
        assert result.estimated_total_pages == 0


class TestDependentsResultInvariant:
    def test_complete_must_equal_reason_absence(self) -> None:
        from datetime import datetime

        import pytest
        from pydantic import ValidationError

        from dep_rank.core.models import DependentType, ScrapeReason

        base_dt = datetime(2026, 1, 1)  # noqa: DTZ001
        with pytest.raises(ValidationError):
            DependentsResult(
                source="https://github.com/x/y",
                total_count=0,
                filtered_count=0,
                repos=[],
                dependent_type=DependentType.REPOSITORY,
                scraped_at=base_dt,
                complete=False,
                reason=None,
            )
        with pytest.raises(ValidationError):
            DependentsResult(
                source="https://github.com/x/y",
                total_count=0,
                filtered_count=0,
                repos=[],
                dependent_type=DependentType.REPOSITORY,
                scraped_at=base_dt,
                complete=True,
                reason=ScrapeReason.NETWORK_FAILURE,
            )

    @pytest.mark.parametrize(
        "reason",
        [
            ScrapeReason.MAX_PAGES_REACHED,
            ScrapeReason.TREND_CONVERGED,
            ScrapeReason.NETWORK_FAILURE,
            ScrapeReason.RATE_LIMITED,
        ],
    )
    def test_every_reason_marks_incomplete(self, reason: ScrapeReason) -> None:
        """All four terminal reasons construct cleanly on the user-facing model and
        satisfy the invariant."""
        from datetime import datetime

        from dep_rank.core.models import DependentType

        result = DependentsResult(
            source="https://github.com/x/y",
            total_count=0,
            filtered_count=0,
            repos=[],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime(2026, 1, 1),  # noqa: DTZ001
            complete=False,
            reason=reason,
        )
        assert result.complete is False
        assert result.reason == reason


class TestScrapeSnapshot:
    def test_per_page_snapshot_defaults(self) -> None:
        from dep_rank.core.models import ScrapeSnapshot

        snap = ScrapeSnapshot(
            top_k=[],
            pages_scraped=1,
            estimated_total_pages=3,
            estimated_total_dependents=90,
            matched_count=2,
        )
        assert snap.done is False
        assert snap.complete is False
        assert snap.reason is None

    def test_terminal_snapshot(self) -> None:
        from dep_rank.core.models import ScrapeReason, ScrapeSnapshot

        snap = ScrapeSnapshot(
            top_k=[],
            pages_scraped=200,
            estimated_total_pages=500,
            estimated_total_dependents=15000,
            matched_count=4200,
            done=True,
            complete=False,
            reason=ScrapeReason.MAX_PAGES_REACHED,
        )
        assert snap.done is True
        assert snap.complete is False
        assert snap.reason == "max_pages_reached"


class TestTrustModels:
    def test_repository_trust_fields_default_none(self) -> None:
        repo = Repository(owner="a", name="b", url="https://github.com/a/b", stars=1)
        assert repo.trust is None
        assert repo.trust_signals is None

    def test_trust_signals_excluded_from_serialization(self) -> None:
        repo = Repository(
            owner="a",
            name="b",
            url="https://github.com/a/b",
            stars=1,
            trust_signals=TrustSignals(forks=5, issues=3, pull_requests=2, pushed_at=None),
        )
        assert "trust_signals" not in repo.model_dump_json()
        assert "trust_signals" not in repo.model_dump()

    def test_trust_score_serializes_with_components(self) -> None:
        repo = Repository(
            owner="a",
            name="b",
            url="https://github.com/a/b",
            stars=1,
            trust=TrustScore(
                score=72.5,
                forks=5,
                issues=3,
                pull_requests=2,
                pushed_at=None,
                components=TrustComponents(stars=0.5, forks=0.4, engagement=0.3, recency=0.0),
            ),
        )
        data = repo.model_dump_json()
        assert '"score":72.5' in data
        assert '"components"' in data

    def test_dependents_result_ranked_by_defaults_to_stars(self) -> None:
        result = DependentsResult(
            source="https://github.com/x/y",
            total_count=0,
            filtered_count=0,
            repos=[],
            dependent_type=DependentType.REPOSITORY,
            scraped_at=datetime.now(tz=UTC),
        )
        assert result.ranked_by == "stars"

    def test_trust_metadata_result_fields(self) -> None:
        res = TrustMetadataResult(repos=[], failed=False, complete=True)
        assert res.repos == []
        assert res.failed is False
        assert res.complete is True
