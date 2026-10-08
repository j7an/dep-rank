"""GitHub GraphQL API for batched repository metadata (stars, description, trust signals)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx2

from dep_rank.core.models import Repository, TrustMetadataResult, TrustSignals
from dep_rank.core.scraper import REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
# GitHub ends an over-budget query with RESOURCE_LIMITS_EXCEEDED and returns null for every
# alias past the cutoff; one 74-repo query was cut off at alias ~41, driven by the issue/PR
# totalCount fields. Smaller batches cost no extra wall time (server work scales per repo).
# ponytail: a fixed batch size is enough; re-query null aliases in smaller batches if
# RESOURCE_LIMITS_EXCEEDED is ever observed at this size.
BATCH_SIZE = 25


def build_trust_query(repos: list[Repository], *, include_description: bool) -> str:
    """Build a GraphQL query for trust metadata across multiple repos.

    Fetches accurate stars plus low-cost engagement/recency/status signals (the extra
    scalars cost no additional rate-limit points). The ``states:``
    filters are stated explicitly (exhaustive enums) so the intent — all-time totals —
    cannot drift. When ``include_description`` is set, also fetches description so a
    combined ``--rank-by trust --descriptions`` run needs only one GraphQL pass.
    """
    desc = " description" if include_description else ""
    fragments: list[str] = []
    for i, repo in enumerate(repos):
        fragments.append(
            f'repo_{i}: repository(owner: "{repo.owner}", name: "{repo.name}") {{ '
            f"stargazerCount forkCount "
            f"issues(states: [OPEN, CLOSED]) {{ totalCount }} "
            f"pullRequests(states: [OPEN, CLOSED, MERGED]) {{ totalCount }} "
            f"pushedAt isArchived isDisabled createdAt{desc} }}"
        )
    return "query { " + " ".join(fragments) + " }"


def _parse_timestamp(raw: str | None, field: str, repo: Repository) -> datetime | None:
    """Parse a GraphQL DateTime; missing or malformed values degrade to ``None``."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        # A malformed timestamp degrades that signal to "missing" rather than aborting
        # the whole trust run — consistent with the function's other partial handling.
        logger.warning(
            "Unparseable %s %r for %s/%s — treating it as missing",
            field,
            raw,
            repo.owner,
            repo.name,
        )
        return None


def _apply_trust_data(
    repo: Repository, repo_data: dict[str, Any], include_description: bool
) -> Repository:
    """Return a copy of ``repo`` updated with accurate stars + trust signals."""
    signals = TrustSignals(
        forks=repo_data.get("forkCount"),
        issues=(repo_data.get("issues") or {}).get("totalCount"),
        pull_requests=(repo_data.get("pullRequests") or {}).get("totalCount"),
        pushed_at=_parse_timestamp(repo_data.get("pushedAt"), "pushedAt", repo),
        is_archived=repo_data.get("isArchived"),
        is_disabled=repo_data.get("isDisabled"),
        created_at=_parse_timestamp(repo_data.get("createdAt"), "createdAt", repo),
    )
    stars = repo_data.get("stargazerCount")
    update: dict[str, Any] = {
        # Sparse/field-errored repo objects may omit stargazerCount — degrade to the
        # scraped value rather than crash (graceful partial handling).
        "stars": stars if stars is not None else repo.stars,
        "trust_signals": signals,
    }
    if include_description:
        update["description"] = repo_data.get("description")
    return repo.model_copy(update=update)


async def enrich_with_trust_metadata(
    session: httpx2.AsyncClient,
    repos: list[Repository],
    token: str,
    *,
    include_description: bool = False,
) -> TrustMetadataResult:
    """Fetch trust metadata for repos via GraphQL (batches of ``BATCH_SIZE``).

    Status semantics: a 401 short-circuits to ``failed=True`` (token invalid). Ordinary
    batch errors (non-200 / GraphQL error) make those repos pass through with
    ``trust_signals=None`` and set ``complete=False``; they only make ``failed=True``
    when every batch fails. ``complete`` is True only on a clean run.
    """
    if not repos:
        return TrustMetadataResult(repos=[], failed=False, complete=True)

    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
    }
    enriched: list[Repository] = []
    any_success = False
    complete = True

    for batch_start in range(0, len(repos), BATCH_SIZE):
        batch = repos[batch_start : batch_start + BATCH_SIZE]
        query = build_trust_query(batch, include_description=include_description)

        async with session.stream(
            "POST",
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status_code == 401:
                logger.warning("GitHub API authentication failed — token may be expired or invalid")
                return TrustMetadataResult(repos=repos, failed=True, complete=False)
            if resp.status_code != 200:
                logger.warning("GitHub API returned HTTP %d", resp.status_code)
                enriched.extend(batch)
                complete = False
                continue
            await resp.aread()
            data = resp.json()

        if "data" not in data or data["data"] is None:
            message = data.get("message") or data.get("errors", "unknown error")
            logger.warning("GitHub GraphQL error: %s", message)
            enriched.extend(batch)
            complete = False
            continue

        if data.get("errors"):
            # Partial response: usable data alongside per-repo/per-field errors. Keep
            # the data but mark the run incomplete (spec: GraphQL error -> complete=False).
            logger.warning("GitHub GraphQL partial errors: %s", data["errors"])
            complete = False

        for i, repo in enumerate(batch):
            repo_data = data["data"].get(f"repo_{i}")
            if not repo_data:
                enriched.append(repo)
                complete = False
                continue
            enriched.append(_apply_trust_data(repo, repo_data, include_description))
            # success means a repo got usable trust_signals, not merely a data object
            any_success = True

    failed = not any_success
    return TrustMetadataResult(repos=enriched, failed=failed, complete=complete and not failed)
