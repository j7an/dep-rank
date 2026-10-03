"""Tests for the sampled star-history timing heuristic."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from dep_rank.core.models import CautionCode
from dep_rank.core.star_history import evaluate_star_history, parse_star_history

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def daily(start: date, counts: list[int]) -> list[tuple[date, int]]:
    return [(start + timedelta(days=i), count) for i, count in enumerate(counts)]


def test_parse_flattens_weeks_sunday_first() -> None:
    payload = [
        {"week": 1789862400, "total": 3, "days": [1, 0, 0, 0, 0, 0, 2]},
        {"week": 1789257600, "total": 1, "days": [1, 0, 0, 0, 0, 0, 0]},
    ]
    parsed = parse_star_history(payload)
    assert len(parsed) == 14
    assert parsed[0] == (date(2026, 9, 13), 1)
    assert (date(2026, 9, 26), 2) in parsed
    assert parsed == sorted(parsed)


@pytest.mark.parametrize("week", [1789257600 - 9 * 3600, 1789257600 + 7 * 3600])
def test_parse_offset_week_maps_to_sunday(week: int) -> None:
    assert parse_star_history([{"week": week, "total": 1, "days": [1, 0, 0, 0, 0, 0, 0]}])[0] == (
        date(2026, 9, 13),
        1,
    )


def test_parse_empty_list_is_empty() -> None:
    assert parse_star_history([]) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "x"},
        [{"week": 1, "days": [1, 2]}],
        [{"week": "1", "days": [0] * 7}],
        [{"week": 1, "days": [0, 0, 0, 0, 0, 0, None]}],
        [{"days": [0] * 7}],
        [{"week": True, "days": [0] * 7}],
        [{"week": 1, "days": [True] * 7}],
        [None],
        [{"week": 10**30, "days": [0] * 7}],
    ],
)
def test_parse_rejects_malformed(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_star_history(payload)


def test_insufficient_below_floor() -> None:
    verdict = evaluate_star_history(daily(date(2026, 3, 5), [1] * 199), now=NOW)
    assert verdict.sufficient is False
    assert verdict.caution is None


def test_floor_is_inclusive() -> None:
    verdict = evaluate_star_history(daily(date(2026, 3, 5), [1] * 200), now=NOW)
    assert verdict.sufficient is True
    assert verdict.caution is None


@pytest.mark.parametrize("peak, concentrated", [(30, True), (29, False)])
def test_share_threshold_inclusive(peak: int, concentrated: bool) -> None:
    verdict = evaluate_star_history(daily(date(2026, 3, 5), [peak] + [1] * (200 - peak)), now=NOW)
    if concentrated:
        assert verdict.caution is not None
        assert verdict.caution.code == CautionCode.CONCENTRATED_STARRING
    else:
        assert verdict.caution is None


def test_description_cites_counts_and_date() -> None:
    counts = daily(date(2026, 3, 5), [27] * 100 + [71])
    counts.append((date(2026, 9, 13), 1331))
    verdict = evaluate_star_history(counts, now=NOW)
    assert verdict.caution is not None
    assert verdict.caution.description == (
        "1,331 of 4,102 stars in the last 30 weeks arrived on 2026-09-13"
    )


def test_future_days_excluded() -> None:
    counts = daily(date(2026, 3, 5), [1] * 200) + [(date(2026, 9, 21), 500)]
    assert evaluate_star_history(counts, now=NOW).caution is None


def test_non_utc_now_uses_utc_date() -> None:
    now = datetime(2026, 9, 21, 1, tzinfo=timezone(timedelta(hours=5)))
    counts = daily(date(2026, 3, 5), [1] * 200) + [(date(2026, 9, 21), 500)]
    assert evaluate_star_history(counts, now=now).caution is None


def test_peak_tie_picks_earliest() -> None:
    counts = daily(date(2026, 3, 5), [2] * 100)
    counts += [(date(2026, 9, 5), 100), (date(2026, 9, 1), 100)]
    verdict = evaluate_star_history(counts, now=NOW)
    assert verdict.caution is not None
    assert verdict.caution.description.endswith("2026-09-01")


def test_empty_daily_is_insufficient() -> None:
    verdict = evaluate_star_history([], now=NOW)
    assert verdict.sufficient is False
    assert verdict.caution is None
