"""Tests for Pydantic models: only behavior the project defines, not Pydantic itself."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import pytest
from pydantic import ValidationError

from dep_rank.core.models import (
    CautionCode,
    DependentsResult,
    DependentType,
    Repository,
    ScrapeReason,
    ScrapeResult,
    TrustComponents,
    TrustScore,
    TrustSignals,
)

REASONS = list(ScrapeReason)


def _build(
    model: type[ScrapeResult | DependentsResult], **overrides: Any
) -> ScrapeResult | DependentsResult:
    if model is ScrapeResult:
        fields: dict[str, Any] = {
            "repos": [],
            "pages_scraped": 0,
            "max_pages": 200,
            "estimated_total_pages": 0,
            "estimated_total_dependents": 0,
        }
    else:
        fields = {
            "source": "https://github.com/x/y",
            "total_count": 0,
            "filtered_count": 0,
            "repos": [],
            "dependent_type": DependentType.REPOSITORY,
            "scraped_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    return model(**{**fields, **overrides})


class TestRepository:
    def test_create_minimal(self) -> None:
        repo = Repository(
            owner="django", name="django", url="https://github.com/django/django", stars=82000
        )
        assert repo.owner == "django"
        assert repo.name == "django"
        assert repo.stars == 82000
        assert repo.description is None


class TestDependentsResult:
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


@pytest.mark.parametrize(
    ("member", "wire"),
    [
        (ScrapeReason.MAX_PAGES_REACHED, "max_pages_reached"),
        (ScrapeReason.TREND_CONVERGED, "trend_converged"),
        (ScrapeReason.NETWORK_FAILURE, "network_failure"),
        (ScrapeReason.RATE_LIMITED, "rate_limited"),
        (CautionCode.CONCENTRATED_STARRING, "concentrated_starring"),
    ],
)
def test_enum_wire_values(member: StrEnum, wire: str) -> None:
    assert member.value == wire


@pytest.mark.parametrize("model", [ScrapeResult, DependentsResult])
@pytest.mark.parametrize(
    ("complete", "reason", "valid"),
    [
        (True, None, True),
        *[(False, reason, True) for reason in REASONS],
        (True, ScrapeReason.MAX_PAGES_REACHED, False),
        (False, None, False),
    ],
)
def test_complete_iff_no_reason(
    model: type[ScrapeResult | DependentsResult],
    complete: bool,
    reason: ScrapeReason | None,
    valid: bool,
) -> None:
    """The terminal contract enforces ``complete == (reason is None)`` on both models."""
    if not valid:
        with pytest.raises(ValidationError):
            _build(model, complete=complete, reason=reason)
        return
    result = _build(model, complete=complete, reason=reason)
    assert result.complete is complete
    assert result.reason == reason


@pytest.mark.parametrize("model", [ScrapeResult, DependentsResult])
def test_model_defaults(model: type[ScrapeResult | DependentsResult]) -> None:
    result = _build(model)
    assert result.complete is True
    assert result.reason is None
    if isinstance(result, ScrapeResult):
        assert result.matched_count == 0
    else:
        assert result.pages_scraped == 0
        assert result.estimated_total_pages == 0
        assert result.ranked_by == "stars"  # default ranking strategy


def test_ranked_by_rejects_invalid_value() -> None:
    # ranked_by is a Literal["stars", "trust"]; a typo fails fast at construction
    # rather than silently rendering as the star branch downstream.
    with pytest.raises(ValidationError):
        _build(DependentsResult, ranked_by="trsut")


class TestTrustModels:
    def test_status_fields_excluded_from_serialization(self) -> None:
        result = _build(DependentsResult, stale_pages=3, trust_metadata_complete=False)
        assert isinstance(result, DependentsResult)  # _build returns a union; narrow for mypy
        assert result.stale_pages == 3
        assert result.trust_metadata_complete is False
        for dumped in (result.model_dump_json(), str(result.model_dump())):
            assert "stale_pages" not in dumped
            assert "trust_metadata_complete" not in dumped

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
