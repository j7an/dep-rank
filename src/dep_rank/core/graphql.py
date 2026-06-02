"""GitHub GraphQL API for batch repository enrichment."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import Repository, TrustMetadataResult, TrustSignals

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
BATCH_SIZE = 100


def build_batch_query(repos: list[Repository]) -> str:
    """Build a GraphQL query to fetch stargazerCount and description for multiple repos."""
    fragments: list[str] = []
    for i, repo in enumerate(repos):
        fragments.append(
            f'repo_{i}: repository(owner: "{repo.owner}", name: "{repo.name}") '
            f"{{ stargazerCount description }}"
        )
    return "query { " + " ".join(fragments) + " }"


async def enrich_with_graphql(
    session: aiohttp.ClientSession,
    repos: list[Repository],
    token: str,
    cache: SqliteCache | None = None,
) -> list[Repository]:
    """Fetch accurate star counts and descriptions via GitHub GraphQL API.

    Batches repos into groups of 100. Returns a new list sorted by stars descending.
    """
    if not repos:
        return []

    enriched: list[Repository] = []
    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
    }

    for batch_start in range(0, len(repos), BATCH_SIZE):
        batch = repos[batch_start : batch_start + BATCH_SIZE]
        query = build_batch_query(batch)

        async with session.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
        ) as resp:
            if resp.status == 401:
                logger.warning("GitHub API authentication failed — token may be expired or invalid")
                return repos
            if resp.status != 200:
                logger.warning("GitHub API returned HTTP %d", resp.status)
                enriched.extend(batch)
                continue
            data = await resp.json()

        if "data" not in data:
            message = data.get("message", "unknown error")
            logger.warning("GitHub GraphQL error: %s", message)
            enriched.extend(batch)
            continue

        for i, repo in enumerate(batch):
            repo_data = data["data"].get(f"repo_{i}")
            if repo_data:
                enriched.append(
                    repo.model_copy(
                        update={
                            "stars": repo_data["stargazerCount"],
                            "description": repo_data.get("description"),
                        }
                    )
                )
            else:
                enriched.append(repo)

    enriched.sort(key=lambda r: r.stars, reverse=True)
    return enriched


def build_trust_query(repos: list[Repository], *, include_description: bool) -> str:
    """Build a GraphQL query for trust metadata across multiple repos.

    Fetches accurate stars plus low-cost engagement/recency signals. The ``states:``
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
            f"pushedAt{desc} }}"
        )
    return "query { " + " ".join(fragments) + " }"


def _apply_trust_data(
    repo: Repository, repo_data: dict[str, Any], include_description: bool
) -> Repository:
    """Return a copy of ``repo`` updated with accurate stars + trust signals."""
    pushed_at_raw = repo_data.get("pushedAt")
    pushed_at = datetime.fromisoformat(pushed_at_raw) if pushed_at_raw else None
    signals = TrustSignals(
        forks=repo_data.get("forkCount"),
        issues=(repo_data.get("issues") or {}).get("totalCount"),
        pull_requests=(repo_data.get("pullRequests") or {}).get("totalCount"),
        pushed_at=pushed_at,
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
    session: aiohttp.ClientSession,
    repos: list[Repository],
    token: str,
    *,
    include_description: bool = False,
    cache: SqliteCache | None = None,
) -> TrustMetadataResult:
    """Fetch trust metadata for repos via GraphQL (batches of 100).

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

        async with session.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
        ) as resp:
            if resp.status == 401:
                logger.warning("GitHub API authentication failed — token may be expired or invalid")
                return TrustMetadataResult(repos=repos, failed=True, complete=False)
            if resp.status != 200:
                logger.warning("GitHub API returned HTTP %d", resp.status)
                enriched.extend(batch)
                complete = False
                continue
            data = await resp.json()

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
