"""Tests for the pure trust-scoring function."""

from __future__ import annotations

from datetime import UTC, datetime

from dep_rank.core.models import Repository, TrustSignals
from dep_rank.core.trust import compute_trust_scores


def make_repo(
    owner: str,
    stars: int,
    *,
    forks: int | None = None,
    issues: int | None = None,
    prs: int | None = None,
    pushed_at: datetime | None = None,
    has_signals: bool = True,
) -> Repository:
    signals = (
        TrustSignals(forks=forks, issues=issues, pull_requests=prs, pushed_at=pushed_at)
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


def test_empty_pool_returns_empty() -> None:
    assert compute_trust_scores([]) == []


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
    ranked = compute_trust_scores([star_heavy, engaged])
    assert ranked[0].owner == "engaged"


def test_scores_are_0_to_100_and_populated() -> None:
    repos = [
        make_repo("a", 10, forks=1, issues=1, prs=1, pushed_at=datetime(2020, 1, 1, tzinfo=UTC)),
        make_repo("b", 20, forks=2, issues=2, prs=2, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)),
    ]
    ranked = compute_trust_scores(repos)
    for repo in ranked:
        assert repo.trust is not None
        assert 0.0 <= repo.trust.score <= 100.0
        assert repo.trust.components is not None


def test_single_repo_pool_scores_50() -> None:
    # Every component degenerate (single element) -> 0.5 each -> score 50.
    repo = make_repo(
        "solo", 5, forks=5, issues=5, prs=5, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ranked = compute_trust_scores([repo])
    assert ranked[0].trust is not None
    assert ranked[0].trust.score == 50.0


def test_missing_signals_treated_as_weak() -> None:
    # `none` has no signals at all: counts -> 0, recency -> 0.0. It must not outrank
    # a repo with real engagement.
    none = make_repo("none", 50, has_signals=False)
    real = make_repo(
        "real", 50, forks=100, issues=100, prs=100, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ranked = compute_trust_scores([none, real])
    assert ranked[0].owner == "real"
    assert ranked[-1].owner == "none"


def test_missing_pushed_at_gets_zero_recency() -> None:
    # Same counts; only difference is one repo has no pushed_at -> 0.0 recency, the
    # other (present, single timestamp) -> degenerate 0.5. The dated repo wins.
    dated = make_repo(
        "dated", 10, forks=10, issues=10, prs=10, pushed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    undated = make_repo("undated", 10, forks=10, issues=10, prs=10, pushed_at=None)
    ranked = compute_trust_scores([dated, undated])
    assert ranked[0].owner == "dated"


def test_deterministic_tie_break() -> None:
    # Identical signals -> identical scores; ties break by stars desc then owner/name.
    a = make_repo("alpha", 10, forks=1, issues=1, prs=1)
    b = make_repo("bravo", 10, forks=1, issues=1, prs=1)
    ranked = compute_trust_scores([b, a])
    assert [r.owner for r in ranked] == ["alpha", "bravo"]
