"""Pure, deterministic trust scoring over a candidate pool.

The score is a pool-relative ranking signal (0-100), not an absolute quality
measure. Pool-relative recency is a min-max over ``pushed_at`` and never references
the clock. Caution signals are per-repo absolute heuristics; the age-based ones
measure against an injected ``now`` so results stay deterministic.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from dep_rank.core.models import (
    CautionCode,
    CautionSignal,
    Repository,
    TrustComponents,
    TrustScore,
)

# Component weights (sum to 1.0). Stars <= 0.40; non-star signals are the majority.
_W_STARS = 0.35
_W_FORKS = 0.25
_W_ENGAGEMENT = 0.20
_W_RECENCY = 0.20

# Caution thresholds. Heuristic, informational only — never a fake-star verdict.
# The star floors act as a minimum sample size; ratios as the meaningful share.
_CAUTION_MIN_STARS = 500
_CAUTION_MAX_RATIO = 0.01  # forks/stars AND (issues+PRs)/stars must both fall below
_STALE_AFTER = timedelta(days=365)
_NEW_REPO_MIN_STARS = 1000
_NEW_REPO_WITHIN = timedelta(days=180)


def _minmax(values: list[float]) -> list[float]:
    """Min-max normalize to 0.0-1.0. Degenerate (all equal) -> 0.5 for every element."""
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [0.5] * len(values)
    span = hi - lo
    return [(v - lo) / span for v in values]


def _log_component(raws: list[int]) -> list[float]:
    """log1p each raw count, then min-max. Missing raws must already be 0."""
    return _minmax([math.log1p(v) for v in raws])


def _recency_component(repos: list[Repository]) -> list[float]:
    """Pool-relative recency from ``pushed_at``.

    Missing ``pushed_at`` is forced to 0.0 and never enters the min-max. Present
    timestamps are min-max'd; a degenerate present range yields 0.5 for those repos.
    """
    result = [0.0] * len(repos)
    present: list[tuple[int, float]] = []
    for i, repo in enumerate(repos):
        sig = repo.trust_signals
        if sig is not None and sig.pushed_at is not None:
            present.append((i, sig.pushed_at.timestamp()))
    if not present:
        return result
    normed = _minmax([epoch for _, epoch in present])
    for (i, _), value in zip(present, normed, strict=True):
        result[i] = value
    return result


def _caution_signals(repo: Repository, now: datetime) -> list[CautionSignal]:
    """Return the caution signals ``repo`` meets. Missing inputs never fire a signal."""
    sig = repo.trust_signals
    if sig is None:
        return []
    stars = repo.stars
    cautions: list[CautionSignal] = []

    if (
        stars >= _CAUTION_MIN_STARS
        and sig.forks is not None
        and sig.issues is not None
        and sig.pull_requests is not None
    ):
        engagement = sig.issues + sig.pull_requests
        if sig.forks / stars < _CAUTION_MAX_RATIO and engagement / stars < _CAUTION_MAX_RATIO:
            cautions.append(
                CautionSignal(
                    code=CautionCode.LOW_NON_STAR_ACTIVITY,
                    description=(
                        f"{sig.forks:,} forks and {engagement:,} issues/PRs for {stars:,} stars"
                    ),
                )
            )

    if stars >= _CAUTION_MIN_STARS and sig.pushed_at is not None:
        idle = now - sig.pushed_at
        if idle > _STALE_AFTER:
            cautions.append(
                CautionSignal(
                    code=CautionCode.STALE_ACTIVITY,
                    description=f"No pushes in {idle.days:,} days",
                )
            )

    if sig.is_archived or sig.is_disabled:
        status = "archived" if sig.is_archived else "disabled"
        cautions.append(
            CautionSignal(
                code=CautionCode.ARCHIVED_OR_DISABLED,
                description=f"Repository is {status}",
            )
        )

    if stars >= _NEW_REPO_MIN_STARS and sig.created_at is not None:
        age = now - sig.created_at
        if age < _NEW_REPO_WITHIN:
            cautions.append(
                CautionSignal(
                    code=CautionCode.NEW_WITH_HIGH_STARS,
                    description=f"Created {age.days:,} days ago with {stars:,} stars",
                )
            )

    return cautions


def compute_trust_scores(repos: list[Repository], *, now: datetime) -> list[Repository]:
    """Score each repo 0-100 relative to the pool; return them sorted by score desc.

    Repos should carry ``trust_signals``; missing signals are scored as weak (counts
    -> 0 before normalization, missing ``pushed_at`` -> 0.0 recency). Ties break by
    stars desc, then ``owner/name`` ascending, for stable ordering. ``now`` is the
    reference time for age-based caution signals only; it never affects the score.
    """
    if not repos:
        return []

    stars_c = _log_component([r.stars for r in repos])
    forks_c = _log_component(
        [(r.trust_signals.forks or 0) if r.trust_signals else 0 for r in repos]
    )
    engagement_c = _log_component(
        [
            ((r.trust_signals.issues or 0) + (r.trust_signals.pull_requests or 0))
            if r.trust_signals
            else 0
            for r in repos
        ]
    )
    recency_c = _recency_component(repos)

    scored: list[tuple[float, Repository]] = []
    for i, repo in enumerate(repos):
        components = TrustComponents(
            stars=stars_c[i],
            forks=forks_c[i],
            engagement=engagement_c[i],
            recency=recency_c[i],
        )
        score = 100.0 * (
            _W_STARS * components.stars
            + _W_FORKS * components.forks
            + _W_ENGAGEMENT * components.engagement
            + _W_RECENCY * components.recency
        )
        sig = repo.trust_signals
        trust = TrustScore(
            score=score,
            forks=sig.forks if sig else None,
            issues=sig.issues if sig else None,
            pull_requests=sig.pull_requests if sig else None,
            pushed_at=sig.pushed_at if sig else None,
            components=components,
            cautions=_caution_signals(repo, now),
        )
        scored.append((score, repo.model_copy(update={"trust": trust})))

    scored.sort(key=lambda t: (-t[0], -t[1].stars, f"{t[1].owner}/{t[1].name}"))
    return [repo for _, repo in scored]
