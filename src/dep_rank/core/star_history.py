"""Sampled star-timing heuristic, never a fake-star verdict.

Stargazer identities are unavailable under GitHub's July 2026 restriction; this
check uses aggregate daily counts only.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

import aiohttp

from dep_rank.core.models import CautionCode, CautionSignal, Repository, TrustCheckResult

logger = logging.getLogger(__name__)

STAR_HISTORY_URL = "https://api.github.com/repos/{owner}/{name}/stargazers/history"
WINDOW_WEEKS = 30
MAX_CHECKED_REPOS = 25
MIN_WINDOW_STARS = 200
CONCENTRATION_SHARE = 0.15
API_VERSION = "2026-03-10"


class StarHistoryVerdict(NamedTuple):
    """Whether sampled history suffices, and its optional timing caution."""

    sufficient: bool
    caution: CautionSignal | None


def parse_star_history(payload: object) -> list[tuple[date, int]]:
    """Flatten Sunday-first weekly counts, rejecting malformed history."""
    if not isinstance(payload, list):
        raise ValueError("Star history must be a list of weeks")
    daily: list[tuple[date, int]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("Star history week must be an object")
        week = entry.get("week")
        days = entry.get("days")
        if type(week) is not int or not isinstance(days, list) or len(days) != 7:
            raise ValueError("Star history requires an integer week and seven daily counts")
        if any(type(count) is not int for count in days):
            raise ValueError("Star history daily counts must be integers")
        try:
            week_date = datetime.fromtimestamp(week, UTC).date()
            # Boundaries need not align with UTC: pick the nearest Sunday.
            offset = (6 - week_date.weekday() + 3) % 7 - 3
            sunday = week_date + timedelta(days=offset)
            daily.extend((sunday + timedelta(days=i), count) for i, count in enumerate(days))
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("Star history week is outside the supported date range") from exc
    return sorted(daily)


def evaluate_star_history(daily: list[tuple[date, int]], *, now: datetime) -> StarHistoryVerdict:
    """Evaluate concentration within supplied sampled history, excluding future days."""
    today = now.astimezone(UTC).date()
    observed = [(day, count) for day, count in daily if day <= today]
    total = sum(count for _, count in observed)
    if total < MIN_WINDOW_STARS:
        return StarHistoryVerdict(sufficient=False, caution=None)
    day, peak = min(observed, key=lambda entry: (-entry[1], entry[0]))
    # ponytail: single-day rule; a campaign spread over a week evades it — add a busiest-7-day-window share check if a real case surfaces.  # noqa: E501
    caution = None
    if peak / total >= CONCENTRATION_SHARE:
        caution = CautionSignal(
            code=CautionCode.CONCENTRATED_STARRING,
            description=(
                f"{peak:,} of {total:,} stars in the last {WINDOW_WEEKS} weeks "
                f"arrived on {day.isoformat()}"
            ),
        )
    return StarHistoryVerdict(sufficient=True, caution=caution)


async def check_star_history(
    session: aiohttp.ClientSession,
    repos: list[Repository],
    token: str,
    *,
    now: datetime,
) -> tuple[list[Repository], TrustCheckResult]:
    """Check the first bounded set sequentially, preserving input order and scores."""
    checked = repos[:MAX_CHECKED_REPOS]
    updated = list(repos)
    unavailable: list[str] = []
    insufficient_history: list[str] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    # The core REST budget is separate from the scraper's request budget/RateLimiter.
    # ponytail: no early stop on 401/403/429 — <= 25 fast failures is acceptable; add one if MAX_CHECKED_REPOS grows substantially.  # noqa: E501
    for index, repo in enumerate(checked):
        full_name = f"{repo.owner}/{repo.name}"
        url = STAR_HISTORY_URL.format(owner=repo.owner, name=repo.name)
        try:
            async with session.get(
                url, params={"per_page": WINDOW_WEEKS}, headers=headers
            ) as response:
                if response.status != 200:
                    unavailable.append(full_name)
                    logger.debug(
                        "Star history unavailable for %s: HTTP %s", full_name, response.status
                    )
                    continue
                daily = parse_star_history(await response.json())
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            unavailable.append(full_name)
            logger.debug("Star history unavailable for %s: %s", full_name, exc)
            continue
        verdict = evaluate_star_history(daily, now=now)
        if not verdict.sufficient:
            insufficient_history.append(full_name)
        if verdict.caution is not None and repo.trust is not None:
            updated[index] = repo.model_copy(
                update={
                    "trust": repo.trust.model_copy(
                        update={"cautions": [*repo.trust.cautions, verdict.caution]}
                    )
                }
            )
    return updated, TrustCheckResult(
        complete=not unavailable,
        window_weeks=WINDOW_WEEKS,
        repos_checked=len(checked),
        insufficient_history=insufficient_history,
        unavailable=unavailable,
    )


def skipped_trust_check(repos: list[Repository]) -> TrustCheckResult:
    """Report the bounded check unavailable when trust ranking could not run."""
    unavailable = [f"{repo.owner}/{repo.name}" for repo in repos[:MAX_CHECKED_REPOS]]
    return TrustCheckResult(
        complete=not unavailable,
        window_weeks=WINDOW_WEEKS,
        repos_checked=len(unavailable),
        unavailable=unavailable,
    )
