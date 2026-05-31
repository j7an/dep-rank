"""Weekly canary: verify GitHub dependents selectors still parse a known repo.

Run via ``uv run python -m dep_rank.scripts.drift_check``. Requires the
``DRIFT_CHECK_TOKEN`` env var (a fine-grained read-only PAT); without it the script
exits 2 rather than running unauthenticated and risking a permanently-blind green check.

Exit codes:
  0 — selectors healthy, or transient/inconclusive run (emits a ``::warning::`` annotation)
  1 — drift detected: item selectors returned no repos, or the header count parsed to zero
  2 — misconfigured: ``DRIFT_CHECK_TOKEN`` not set
"""

from __future__ import annotations

import asyncio
import os
import sys

import aiohttp

from dep_rank.core.models import ScrapeReason
from dep_rank.core.scraper import scrape_dependents

# Transport-level stop reasons mean we could not observe enough HTML to judge selector
# health — they are *inconclusive*, never "drift". Selector/header drift is only a valid
# verdict when the scrape actually reached pages (reason None or MAX_PAGES_REACHED).
_INCONCLUSIVE_REASONS = (ScrapeReason.NETWORK_FAILURE, ScrapeReason.RATE_LIMITED)

CANARY_URL = "https://github.com/pallets/flask"
# min_stars=0 so the canary measures *raw selector health*, not the star filter: any
# parsed dependent proves the item selectors still match GitHub's HTML. A higher
# threshold could legitimately yield zero matches on a healthy page (the first pages
# may list low-star dependents) and false-alarm as "selector drift".
CANARY_MIN_STARS = 0
CANARY_MAX_PAGES = 2


def evaluate_drift(
    repos_count: int,
    total_dependents: int,
    reason: ScrapeReason | None = None,
) -> list[str]:
    """Return a list of human-readable drift problems (empty == healthy).

    A transport-level ``reason`` (``NETWORK_FAILURE``/``RATE_LIMITED``) is *inconclusive*:
    we never saw enough HTML to judge the selectors, so we return ``[]`` rather than
    misreporting a flaky network as selector drift. ``MAX_PAGES_REACHED`` and
    ``TREND_CONVERGED`` are expected on the canary and do not suppress evaluation — the
    pages actually scraped still prove the selectors parse.
    """
    if reason in _INCONCLUSIVE_REASONS:
        return []
    problems: list[str] = []
    if repos_count <= 0:
        problems.append("item selectors returned zero repositories")
    if total_dependents <= 0:
        problems.append("dependents-count header parsed to zero")
    return problems


async def _run() -> int:
    token = os.environ.get("DRIFT_CHECK_TOKEN")
    if not token:
        # Fail loudly on a missing token instead of running unauthenticated. An unauth
        # canary is the silent-blind failure mode: GitHub is far likelier to 429 it, the
        # scrape ends RATE_LIMITED, the run exits 0 "inconclusive", and the check passes
        # every week without ever verifying a selector. A red job is the correct prompt to
        # add the DRIFT_CHECK_TOKEN secret (see spec §7 "authenticated via DRIFT_CHECK_TOKEN").
        sys.stderr.write(
            "::error::DRIFT_CHECK_TOKEN is not set. The drift canary requires an "
            "authenticated token to run reliably; add the repo secret.\n"
        )
        return 2
    async with aiohttp.ClientSession(headers={"User-Agent": "dep-rank-drift/1.0"}) as session:
        result = await scrape_dependents(
            session,
            CANARY_URL,
            min_stars=CANARY_MIN_STARS,
            token=token,
            max_pages=CANARY_MAX_PAGES,
            rows=10,
        )
    if result.reason in _INCONCLUSIVE_REASONS:
        # Transient network/rate failure *with* a token: we genuinely could not judge
        # selector health this run. Don't page (exit 0 — a single flaky GitHub response
        # shouldn't fail the job), but emit a GitHub `::warning::` annotation so the run
        # is a visible non-success signal in the Actions summary rather than a silent green
        # pass. A persistently inconclusive canary therefore shows as a wall of warnings.
        sys.stderr.write(
            f"::warning::INCONCLUSIVE on {CANARY_URL}: scrape ended on "
            f"{result.reason.value}; selector health could not be checked this run.\n"
        )
        return 0
    problems = evaluate_drift(len(result.repos), result.estimated_total_dependents, result.reason)
    if problems:
        sys.stderr.write(f"DRIFT DETECTED on {CANARY_URL}: " + "; ".join(problems) + "\n")
        return 1
    sys.stdout.write(
        f"OK: {len(result.repos)} repos parsed, "
        f"{result.estimated_total_dependents} total dependents reported "
        f"(stop reason: {result.reason.value if result.reason else 'complete'}).\n"
    )
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
