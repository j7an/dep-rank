# dep-rank

Rank GitHub dependents by stars or trust.

[![PyPI](https://img.shields.io/pypi/v/dep-rank)](https://pypi.org/project/dep-rank/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

dep-rank finds the most popular repositories that depend on a given GitHub project. It scrapes GitHub's dependents page, enriches results via the GraphQL API, and works as a command-line tool.

## Quick Start

```bash
pip install dep-rank
dep-rank deps https://github.com/django/django
```

## CLI Reference

### `dep-rank deps` — List top dependents

```bash
dep-rank deps https://github.com/django/django
dep-rank deps https://github.com/django/django --rows 20 --min-stars 100
dep-rank deps https://github.com/django/django --descriptions --format json
dep-rank deps https://github.com/django/django --packages
```

| Option | Default | Description |
|--------|---------|-------------|
| `--rows` | 10 | Number of results |
| `--min-stars` | 5 | Minimum star count filter |
| `--format` | table | Output format: `table` or `json` |
| `--descriptions` | off | Fetch descriptions via GitHub API (requires token) |
| `--packages` | off | Search packages instead of repositories |
| `--token` | `DEP_RANK_TOKEN` | GitHub token |
| `--max-pages` | 200 | Maximum pages to scrape (ceiling 1000) |
| `--concurrency` | 3 | Max concurrent page fetches (1–10) |
| `--no-adaptive-stop` | off | Disable adaptive early-stop; scrape continues until exhaustion or `--max-pages` |
| `--rank-by` | stars | Ranking strategy: `stars` or `trust` (heuristic, requires token) |

### `dep-rank search` — Search code in dependents

```bash
dep-rank search https://github.com/django/django "from django.db import"
dep-rank search https://github.com/django/django "middleware" --max-repos 20
```

| Option | Default | Description |
|--------|---------|-------------|
| `--max-repos` | 10 | Maximum repos to search |
| `--min-stars` | 50 | Only search repos with this many stars |
| `--token` | `DEP_RANK_TOKEN` | GitHub token (required) |
| `--max-pages` | 200 | Maximum pages to scrape (ceiling 1000) |
| `--concurrency` | 3 | Max concurrent page fetches (1–10) |

`search` always runs a bounded non-adaptive top-K scrape (`--no-adaptive-stop` is not exposed; adaptive early-stop is permanently disabled for this command).

### Partial results

A scrape result (`deps`, and the `search` pre-pass) reports whether it finished: results include a `complete` flag and a `reason`. `complete: false` means the scrape stopped early — `max_pages_reached` (raise `--max-pages`), `trend_converged` (the adaptive heuristic judged the top-K stable; use `--no-adaptive-stop` to scrape until exhaustion or `--max-pages`), `network_failure`, or `rate_limited`. `total_count`/`filtered_count` are then lower bounds across the pages actually scraped, not population totals.

### `dep-rank cache` — Manage cache

```bash
dep-rank cache stats    # Show cache size
dep-rank cache clear    # Clear all cached data
```

## Authentication

Set the `DEP_RANK_TOKEN` environment variable with a GitHub personal access token:

```bash
export DEP_RANK_TOKEN=ghp_your_token_here
```

A token is effectively required for non-trivial use: unauthenticated GitHub HTML scraping is limited to ~60 requests/hour per IP, so unauthenticated runs are suitable only for small one-off scrapes. Set `DEP_RANK_TOKEN` to raise the limit.

**What works without a token:**
- `dep-rank deps` — core scraping and star ranking

**What requires a token:**
- `--descriptions` flag — fetches repo descriptions via GitHub GraphQL API
- `--rank-by trust` — fetches engagement/recency metadata via GitHub GraphQL API
- `dep-rank search` — code search across dependents

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens) with `public_repo` scope.

## How It Works

dep-rank uses a three-stage pipeline:

1. **Scrape** — fetches GitHub's `/network/dependents` HTML pages to discover all dependents and their approximate star counts
2. **Enrich** (optional) — one GraphQL batch query fetches accurate star counts and descriptions for the top N results (replaces 100 individual REST API calls)
3. **Present** — returns structured results as a Rich table

Responses are cached in a local SQLite database (`~/.cache/dep-rank/`) with ETag support for conditional requests. Expired pages are served immediately and refreshed in the background (stale-while-revalidate) on authenticated runs.

## Trust Ranking

`dep-rank deps --rank-by trust` re-ranks dependents by a lightweight composite
score instead of raw stars. Stars are useful but [gameable][starscout]; trust
ranking blends stars with non-star signals — forks, total issues and pull
requests, and recency of activity — fetched via low-cost GitHub GraphQL queries
(batched at 100 repositories per request, so a larger pool issues more than one).

```bash
dep-rank deps https://github.com/django/django --rank-by trust --token ghp_...
```

**Important caveats:**

- The score is a **pool-relative ranking signal, not an absolute quality score** —
  it min-max normalizes signals across the scraped candidate set.
- It re-ranks **only the scraped candidate pool** (the star-top-N dependents), not
  every dependent.
- It is **heuristic and does not detect fake stars.** It does not fetch stargazer
  history, GHArchive data, or external fraud datasets.
- Trust ranking scrapes a **larger candidate pool and is therefore deeper and
  slower** than star ranking.
- `--rank-by trust` requires a GitHub token; trust scores appear in `--format json`
  output under each repo's `trust` field.

Motivation that stars are gameable comes from **StarScout** ([repo][starscout],
[preprint](https://arxiv.org/abs/2412.13459),
[ICSE 2026](https://conf.researchr.org/details/icse-2026/icse-2026-research-track/14/Six-Million-Suspected-Fake-Stars-on-GitHub-A-Growing-Spiral-of-Popularity-Contests)).
The low-resource API basis is the
[GitHub GraphQL rate-limit docs](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api).

[starscout]: https://github.com/hehao98/StarScout

## Development

```bash
# Prerequisites: Python 3.11+, uv
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy dep_rank/
```

## Acknowledgments

dep-rank is a full rewrite of [ghtopdep](https://github.com/andriyor/ghtopdep) by [Andriy Orehov](https://github.com/andriyor). The original project is licensed under MIT.

## License

MIT — see [LICENSE](LICENSE) for details.

