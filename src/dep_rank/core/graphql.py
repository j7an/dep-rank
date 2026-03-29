"""GitHub GraphQL API for batch repository enrichment."""

from __future__ import annotations

import aiohttp

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import Repository

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
            data = await resp.json()

        if "data" not in data:
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
