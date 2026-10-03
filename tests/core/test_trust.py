"""Tests for the pure trust-scoring function."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dep_rank.core.models import CautionCode, Repository, TrustSignals
from dep_rank.core.star_history import evaluate_star_history
from dep_rank.core.trust import compute_trust_scores


def make_repo(
    owner: str,
    stars: int,
    *,
    forks: int | None = None,
    issues: int | None = None,
    prs: int | None = None,
    pushed_at: datetime | None = None,
    is_archived: bool | None = None,
    is_disabled: bool | None = None,
    created_at: datetime | None = None,
    has_signals: bool = True,
) -> Repository:
    signals = (
        TrustSignals(
            forks=forks,
            issues=issues,
            pull_requests=prs,
            pushed_at=pushed_at,
            is_archived=is_archived,
            is_disabled=is_disabled,
            created_at=created_at,
        )
        if has_signals
        else None
    )
    return Repository(
        owner=owner,
        name="r",
        url=f"https://github.com/{owner}/r",
        stars=stars,
        trust_signals=signals,
    )


NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_empty_pool_returns_empty() -> None:
    assert compute_trust_scores([], now=NOW) == []


def test_engagement_can_outrank_stars() -> None:
    # `low_star` has far more forks/issues/PRs and more recent activity; with stars
    # weighted only 0.35 it should outrank the star-heavy but engagement-poor repo.
    star_heavy = make_repo(
        "starheavy", 100_000, forks=1, issues=0, prs=0, pushed_at=datetime(2015, 1, 1, tzinfo=UTC)
    )
    engaged = make_repo(
        "engaged",
        100,
        forks=5_000,
        issues=4_000,
        prs=4_000,
        pushed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    ranked = compute_trust_scores([star_heavy, engaged], now=NOW)
    assert ranked[0].owner == "engaged"


def test_scores_are_0_to_100_and_populated() -> None:
    repos = [
        make_repo("a", 10, forks=1, issues=1, prs=1, pushed_at=datetime(2020, 1, 1, tzinfo=UTC)),
        make_repo("b", 20, forks=2, issues=2, prs=2, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)),
    ]
    ranked = compute_trust_scores(repos, now=NOW)
    for repo in ranked:
        assert repo.trust is not None
        assert 0.0 <= repo.trust.score <= 100.0
        assert repo.trust.components is not None


def test_single_repo_pool_scores_50() -> None:
    # Every component degenerate (single element) -> 0.5 each -> score 50.
    repo = make_repo(
        "solo", 5, forks=5, issues=5, prs=5, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ranked = compute_trust_scores([repo], now=NOW)
    assert ranked[0].trust is not None
    assert ranked[0].trust.score == 50.0


def test_missing_signals_treated_as_weak() -> None:
    # `none` has no signals at all: counts -> 0, recency -> 0.0. It must not outrank
    # a repo with real engagement.
    none = make_repo("none", 50, has_signals=False)
    real = make_repo(
        "real", 50, forks=100, issues=100, prs=100, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ranked = compute_trust_scores([none, real], now=NOW)
    assert ranked[0].owner == "real"
    assert ranked[-1].owner == "none"


def test_missing_pushed_at_gets_zero_recency() -> None:
    # Same counts; only difference is one repo has no pushed_at -> 0.0 recency, the
    # other (present, single timestamp) -> degenerate 0.5. The dated repo wins.
    dated = make_repo(
        "dated", 10, forks=10, issues=10, prs=10, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    undated = make_repo("undated", 10, forks=10, issues=10, prs=10, pushed_at=None)
    ranked = compute_trust_scores([dated, undated], now=NOW)
    assert ranked[0].owner == "dated"


def test_deterministic_tie_break() -> None:
    # Identical signals -> identical scores; ties break by stars desc then owner/name.
    a = make_repo("alpha", 10, forks=1, issues=1, prs=1)
    b = make_repo("bravo", 10, forks=1, issues=1, prs=1)
    ranked = compute_trust_scores([b, a], now=NOW)
    assert [r.owner for r in ranked] == ["alpha", "bravo"]


def caution_codes(repo: Repository) -> list[CautionCode]:
    ranked = compute_trust_scores([repo], now=NOW)
    assert ranked[0].trust is not None
    return [c.code for c in ranked[0].trust.cautions]


def make_healthy(stars: int, *, created_at: datetime | None = None) -> Repository:
    return make_repo(
        "ok", stars, forks=1_000, issues=300, prs=300, pushed_at=NOW, created_at=created_at
    )


def test_healthy_repo_has_no_cautions() -> None:
    assert caution_codes(make_healthy(10_000)) == []


@pytest.mark.parametrize(
    ("stars", "forks", "issues", "prs", "expected"),
    [
        (500, 4, 2, 2, [CautionCode.LOW_NON_STAR_ACTIVITY]),  # 0.008 forks/★ and eng/★
        (499, 0, 0, 0, []),  # below the star floor
        (500, 5, 2, 2, []),  # forks/★ exactly 0.01 is not below the bar
        (500, 4, 3, 2, []),  # (issues+PRs)/★ exactly 0.01 is not below the bar
        # Content/list repos: tiny engagement ratio but healthy forks -> not flagged.
        (10_000, 700, 30, 20, []),
    ],
)
def test_low_non_star_activity(
    stars: int, forks: int, issues: int, prs: int, expected: list[CautionCode]
) -> None:
    repo = make_repo("r", stars, forks=forks, issues=issues, prs=prs, pushed_at=NOW)
    assert caution_codes(repo) == expected


@pytest.mark.parametrize(("forks", "issues", "prs"), [(None, 0, 0), (0, None, 0), (0, 0, None)])
def test_low_non_star_activity_needs_all_inputs(
    forks: int | None, issues: int | None, prs: int | None
) -> None:
    repo = make_repo("r", 5_000, forks=forks, issues=issues, prs=prs, pushed_at=NOW)
    assert caution_codes(repo) == []


@pytest.mark.parametrize(
    ("stars", "age_days", "expected"),
    [
        (500, 366, [CautionCode.STALE_ACTIVITY]),
        (500, 365, []),  # exactly one year is not "more than" a year
        (499, 3_000, []),  # below the star floor
    ],
)
def test_stale_activity(stars: int, age_days: int, expected: list[CautionCode]) -> None:
    repo = make_repo(
        "r", stars, forks=1_000, issues=300, prs=300, pushed_at=NOW - timedelta(days=age_days)
    )
    assert caution_codes(repo) == expected


@pytest.mark.parametrize(
    ("archived", "disabled", "expected"),
    [
        (True, False, [CautionCode.ARCHIVED_OR_DISABLED]),
        (False, True, [CautionCode.ARCHIVED_OR_DISABLED]),
        (False, False, []),
        (None, None, []),
    ],
)
def test_archived_or_disabled(
    archived: bool | None, disabled: bool | None, expected: list[CautionCode]
) -> None:
    repo = make_repo("r", 10, is_archived=archived, is_disabled=disabled)
    assert caution_codes(repo) == expected


@pytest.mark.parametrize(
    ("stars", "age_days", "expected"),
    [
        (1_000, 179, [CautionCode.NEW_WITH_HIGH_STARS]),
        (1_000, 180, []),  # 180 days old is no longer "within 180 days"
        (999, 10, []),  # below the star floor
    ],
)
def test_new_with_high_stars(stars: int, age_days: int, expected: list[CautionCode]) -> None:
    repo = make_healthy(stars, created_at=NOW - timedelta(days=age_days))
    assert caution_codes(repo) == expected


def test_missing_metadata_creates_no_cautions() -> None:
    assert caution_codes(make_repo("r", 50_000, has_signals=False)) == []
    assert caution_codes(make_repo("r", 50_000)) == []  # signals object, every field None


def test_caution_descriptions_are_not_accusatory() -> None:
    repo = make_repo(
        "r",
        5_000,
        forks=1,
        issues=0,
        prs=0,
        pushed_at=NOW - timedelta(days=400),
        is_archived=True,
        created_at=NOW - timedelta(days=30),
    )
    ranked = compute_trust_scores([repo], now=NOW)
    assert ranked[0].trust is not None
    star_history = evaluate_star_history([(NOW.date(), 200)], now=NOW)
    assert star_history.caution is not None
    cautions = [*ranked[0].trust.cautions, star_history.caution]
    assert {c.code for c in cautions} == set(CautionCode)
    for caution in cautions:
        text = caution.description.lower()
        assert caution.description
        assert not any(w in text for w in ("fake", "fraud", "malicious", "suspicious"))
