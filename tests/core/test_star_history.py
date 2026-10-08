"""Tests for the sampled star-history timing heuristic."""

from datetime import UTC, date, datetime, timedelta, timezone

import httpx2
import pytest

from dep_rank.core.models import (
    CautionCode,
    CautionSignal,
    Repository,
    TrustCheckResult,
    TrustComponents,
    TrustScore,
)
from dep_rank.core.star_history import (
    check_star_history,
    evaluate_star_history,
    parse_star_history,
    skipped_trust_check,
)
from tests.conftest import FakeHTTP

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


def history_repo(name: str) -> Repository:
    return Repository(
        owner="o",
        name=name,
        url=f"https://github.com/o/{name}",
        stars=5000,
        trust=TrustScore(
            score=87.4,
            components=TrustComponents(stars=0.9, forks=0.8, engagement=0.7, recency=0.5),
            cautions=[
                CautionSignal(code=CautionCode.STALE_ACTIVITY, description="Existing caution")
            ],
        ),
    )


def history_url(name: str) -> str:
    return f"https://api.github.com/repos/o/{name}/stargazers/history?per_page=30"


def concentrated_payload() -> list[dict[str, object]]:
    return [
        {"week": 1788652800, "total": 2771, "days": [400, 400, 400, 400, 400, 400, 371]},
        {"week": 1789257600, "total": 1331, "days": [1331, 0, 0, 0, 0, 0, 0]},
    ]


class TestCheckStarHistory:
    async def test_concentrated_caution_merged_into_trust(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repo = history_repo("a")
        mock_http.get(history_url("a"), payload=concentrated_payload())
        repos, result = await check_star_history(session, [repo], "tok", now=NOW)
        assert repos[0].trust is not None
        assert repo.trust is not None
        assert repos[0].trust.cautions[:-1] == repo.trust.cautions
        assert len(repo.trust.cautions) == 1
        assert repos[0].trust.cautions[-1].code == CautionCode.CONCENTRATED_STARRING
        assert repos[0].trust.cautions[-1].description == (
            "1,331 of 4,102 stars in the last 30 weeks arrived on 2026-09-13"
        )
        assert repos[0].trust.score == repo.trust.score
        assert result.complete is True
        assert result.repos_checked == 1

    async def test_request_carries_api_version_and_bearer(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(history_url("a"), payload=[])
        await check_star_history(session, [history_repo("a")], "tok", now=NOW)
        headers = next(iter(mock_http.requests.values()))[0].headers
        # contract literal: documented API version for this endpoint
        assert headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert headers["Authorization"] == "Bearer tok"
        assert headers["Accept"] == "application/vnd.github+json"

    async def test_caps_at_25_repos(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        repos = [history_repo(str(i)) for i in range(40)]
        for i in range(25):
            mock_http.get(history_url(str(i)), payload=[])
        returned, result = await check_star_history(session, repos, "tok", now=NOW)
        assert sum(len(calls) for calls in mock_http.requests.values()) == 25
        assert result.repos_checked == 25
        assert result.complete is True
        assert returned == repos
        assert [repo.name for repo in returned] == [str(i) for i in range(40)]

    async def test_mixed_failures_are_unavailable(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repos = [history_repo(name) for name in "abcde"]
        mock_http.get(history_url("a"), payload=concentrated_payload())
        mock_http.get(history_url("b"), status=404)
        mock_http.get(history_url("c"), exception=httpx2.ConnectError("network failure"))
        mock_http.get(history_url("d"), payload={"message": "x"})
        mock_http.get(history_url("e"), payload=[])
        returned, result = await check_star_history(session, repos, "tok", now=NOW)
        assert result.unavailable == ["o/b", "o/c", "o/d"]
        assert result.insufficient_history == ["o/e"]
        assert result.complete is False
        assert result.repos_checked == 5
        assert returned[1:] == repos[1:]

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_error_status_does_not_stop_later_checks(
        self, status: int, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(history_url("a"), status=status)
        mock_http.get(history_url("b"), payload=[])
        _, result = await check_star_history(
            session, [history_repo("a"), history_repo("b")], "tok", now=NOW
        )
        assert result.unavailable == ["o/a"]
        assert result.insufficient_history == ["o/b"]
        assert result.repos_checked == 2

    async def test_timeout_and_bad_json_are_unavailable(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(history_url("a"), exception=httpx2.ReadTimeout("slow"))
        mock_http.get(history_url("b"), body="{", content_type="application/json")
        _, result = await check_star_history(
            session, [history_repo("a"), history_repo("b")], "tok", now=NOW
        )
        assert result.unavailable == ["o/a", "o/b"]
        assert result.complete is False

    async def test_concentrated_repo_without_trust_is_preserved(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repo = history_repo("a").model_copy(update={"trust": None})
        mock_http.get(history_url("a"), payload=concentrated_payload())
        returned, result = await check_star_history(session, [repo], "tok", now=NOW)
        assert returned == [repo]
        assert returned[0].trust is None
        assert result.complete is True
        assert result.insufficient_history == []

    async def test_sufficient_spread_history_preserves_existing_cautions(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        repos = [history_repo("a")]
        payload = [
            {"week": 1788652800, "total": 210, "days": [30] * 7},
            {"week": 1789257600, "total": 210, "days": [30] * 7},
        ]
        mock_http.get(history_url("a"), payload=payload)
        returned, result = await check_star_history(session, repos, "tok", now=NOW)
        assert returned == repos
        assert result.complete is True
        assert result.insufficient_history == []

    async def test_empty_repos(self, mock_http: FakeHTTP, session: httpx2.AsyncClient) -> None:
        returned = await check_star_history(session, [], "tok", now=NOW)
        assert mock_http.requests == {}
        assert returned == ([], TrustCheckResult(complete=True, window_weeks=30, repos_checked=0))

    def test_skipped_trust_check_marks_top_25_unavailable(self) -> None:
        repos = [history_repo(str(i)) for i in range(30)]
        result = skipped_trust_check(repos)
        assert result.repos_checked == 25
        assert result.unavailable == [f"o/{i}" for i in range(25)]
        assert result.complete is False
        assert result.insufficient_history == []
        assert skipped_trust_check([]).complete is True
