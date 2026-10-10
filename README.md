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
| `--no-adaptive-stop` | off | Disable adaptive early-stop; scrape continues until exhaustion or `--max-pages` |
| `--rank-by` | stars | Ranking strategy: `stars` or `trust` (heuristic, requires token) |
| `--trust-check` | off | Check sampled star history for up to 25 top results (requires `--rank-by trust` and token) |
| `--downloads` | off | Show each dependent's most-downloaded registry package (ecosyste.ms / deps.dev / npm; display only) |

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

`search` always runs a bounded non-adaptive top-K scrape (`--no-adaptive-stop` is not exposed; adaptive early-stop is permanently disabled for this command).

### Partial results

A scrape result (`deps`, and the `search` pre-pass) reports whether it finished: results include a `complete` flag and a `reason`. `complete: false` means the scrape stopped early — `max_pages_reached` (raise `--max-pages`), `trend_converged` (the adaptive heuristic judged the top-K stable; use `--no-adaptive-stop` to scrape until exhaustion or `--max-pages`), `network_failure`, or `rate_limited`. `total_count`/`filtered_count` are then lower bounds across the pages actually scraped, not population totals.

### `dep-rank cache` — Manage cache

```bash
dep-rank cache stats    # Show cache size
dep-rank cache clear    # Clear all cached data
```

## Python API (provisional)

Use `dep_rank.core.dependents.get_dependents` for the `deps` pipeline in Python.
It returns `dep_rank.core.models.DependentsResult`; select repository or package
dependents with `dep_rank.core.models.DependentType`. The API may change during 0.x.

```python
import asyncio

import httpx2

from dep_rank.core.cache import SqliteCache
from dep_rank.core.dependents import get_dependents
from dep_rank.core.models import DependentsResult, DependentType


async def main(cache_dir: str | None = None) -> None:
    cache = SqliteCache(cache_dir) if cache_dir is not None else None
    try:
        if cache is not None:
            await cache.initialize()
        async with httpx2.AsyncClient() as session:
            result: DependentsResult = await get_dependents(
                session,
                "https://github.com/django/django",
                dependent_type=DependentType.REPOSITORY,
                cache=cache,
            )
            for repo in result.repos:
                print(repo.url, repo.stars)
            if not result.complete:
                print("Partial results:", result.reason)
    finally:
        if cache is not None:
            await cache.close()


asyncio.run(main(cache_dir=".dep-rank-cache"))
```

`dep_rank.core.cache.SqliteCache(path)` takes a cache directory. Caching is
optional: call `main()` in this example to omit it. The caller opens and closes
the session and initializes and closes the cache; `get_dependents` closes neither.
The defaults return up to 10 dependents with at least 5 stars, ranked by stars.
Pass a GitHub token explicitly as `token=...` for descriptions or trust ranking;
the library entry point does not read `DEP_RANK_TOKEN`. Pass `downloads=True`
for optional package download statistics; no token is required for this lookup.
Check `result.downloads_check.complete` and `.unavailable` to distinguish lookup
failures from repositories with no matching package.

`get_dependents` raises `ValueError` for an invalid GitHub repository URL,
`rank_by` other than `"stars"` or `"trust"`, `descriptions=True` without a token,
`rank_by="trust"` without a token, or `trust_check=True` without
`rank_by="trust"`. Trust scores are pool-relative heuristics, not quality or fraud
verdicts.

Check `result.complete` and `result.reason` for partial results; counts are lower
bounds when incomplete. `result.stale_pages` counts pages served from expired
cache entries, and `result.trust_metadata_complete` is false when trust scores
used partial metadata. `result.trust_pool_size` is the number of dependents scored
together, which trust scores are relative to (0 when not trust-ranked). These fields
exist on the result but are excluded from `model_dump()` and JSON serialization
(`model_dump_json()`).
If trust metadata cannot be fetched, results fall back to stars; check
`result.ranked_by == "trust"` to confirm trust ranking, since
`trust_metadata_complete` describes only metadata used for successful trust ranking.

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
- `--trust-check` — adds authenticated REST star-history requests to trust ranking
- `dep-rank search` — code search across dependents

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens) with `public_repo` scope.

## How It Works

dep-rank uses a three-stage pipeline:

1. **Scrape** — fetches GitHub's `/network/dependents` HTML pages to discover the dependents GitHub lists and their approximate star counts
2. **Enrich** (optional) — one GraphQL batch query fetches accurate star counts and descriptions for the top N results (replaces 100 individual REST API calls)
3. **Present** — returns structured results as a Rich table

With `--downloads`, the displayed rows receive optional package download statistics after ranking.

GitHub only lists repositories whose dependency graph is enabled. Since June 2025 the graph defaults to off for new public repositories and is disabled on long-inactive ones, so counts can miss real dependents.

Responses are cached in a local SQLite database (`~/Library/Caches/dep-rank` on macOS, `$XDG_CACHE_HOME/dep-rank` or `~/.cache/dep-rank` on Linux, and `%LOCALAPPDATA%\dep-rank\dep-rank\Cache` on Windows) with ETag support for conditional requests. Expired pages are served immediately and refreshed in the background (stale-while-revalidate) on authenticated runs. Unauthenticated runs serve expired pages as-is and never refresh them; `deps` prints a notice on stderr when that happens. Set a token or run `dep-rank cache clear` for current results.

## Package downloads

```bash
dep-rank deps https://github.com/django/django --downloads
```

`--downloads` works with both `--repositories` and `--packages`, with no token
requirement. For each displayed dependent, [ecosyste.ms](https://packages.ecosyste.ms/)
provides package candidates and non-npm counts, and [deps.dev](https://deps.dev/)
provides attestation evidence. npm counts come from npm's download API
(`api.npmjs.org`, last 30 days); non-npm counts are ecosyste.ms snapshots that can lag.
The package with the highest download count among accepted candidates is shown;
counts never affect star or trust ranking.

A package is accepted when any of these rules holds:

- deps.dev links the package to the repository through a publish or SLSA
  attestation with at least one attestation marked `verified: true`.
- A package name without a namespace matches the repository name after lowercasing,
  removing `-`, `_`, and `.`, and optionally stripping one recognized language affix.
- A namespaced package (`@scope/name`, `vendor/name`, `publisher/name`) matches only
  when its namespace matches the repository owner under the same normalization;
  matching only the name after the namespace is insufficient.

Packages marked `removed` are rejected even when attested. A ✓ means a verified
attested repository-to-package link; it does not prove how the package was built.
Unmarked packages matched by name alone are unverified: check before installing.
A squatter may register the matching name or an unclaimed owner scope or vendor namespace. Still-listed
malware has no structured flag in these lookup results, and legitimate packages can
later be compromised. dep-rank is not a vulnerability scanner; use tools such as
`pip-audit`, `npm audit`, or [OSV](https://osv.dev/) to check for known vulnerabilities.

Counts may be inflated or stale, and periods differ: `/mo` means last month,
`total` means all time, and other periods are shown explicitly. Raw counts across
these periods are not directly comparable. Name matching can also miss legitimate
packages (for example, a repository whose package has a different name). A `—`
means no accepted package was found; `unavailable` means lookup failed. Lookup
failures leave the deps run usable and print a warning to stderr, including in
JSON mode.

**Third-party egress:** opting in sends the repository URLs of displayed rows to
ecosyste.ms and deps.dev. Displayed npm package names are also sent to
`api.npmjs.org`. Lookup requests omit caller credentials.

JSON adds a per-repository `downloads` object (or `null`) and a top-level
`downloads_check`; without the flag these fields are omitted. This example is
illustrative: selected packages and counts can change.

```json
{
  "repos": [{
    "owner": "facebook",
    "name": "react",
    "downloads": {
      "ecosystem": "npm",
      "name": "react-is",
      "downloads": 1524448794,
      "period": "last-month",
      "verified": true
    }
  }],
  "downloads_check": {
    "complete": true,
    "unavailable": []
  }
}
```

A `null` download value means no accepted package unless the repository's
`owner/name` appears in `downloads_check.unavailable`; in that case the lookup
failed and `downloads_check.complete` is false.

## Trust Ranking

`dep-rank deps --rank-by trust` re-ranks dependents by a lightweight composite
score instead of raw stars. Stars are useful but [gameable][starscout]; trust
ranking blends stars with non-star signals — forks, total issues and pull
requests, and recency of activity — fetched via low-cost GitHub GraphQL queries
(batched at 25 repositories per request, so a larger pool issues more than one).

```bash
dep-rank deps https://github.com/django/django --rank-by trust --token ghp_...
```

**Important caveats:**

- The score is a **pool-relative ranking signal, not an absolute quality score** —
  it min-max normalizes signals across the scraped candidate set.
- It re-ranks **only the scraped candidate pool** (the star-top-N dependents), not
  every dependent.
- It is **heuristic and does not detect fake stars.** By default it does not fetch star
  history; `--trust-check` optionally samples it. Neither mode fetches GHArchive
  data or external fraud datasets.
- Trust ranking scrapes a **larger candidate pool and is therefore deeper and
  slower** than star ranking.
- `--rank-by trust` requires a GitHub token; trust scores appear in `--format json`
  output under each repo's `trust` field.

### How the score is computed

Trust mode scores the most-starred dependents together, then shows the top `--rows`
of that ranking. The number scored (the pool) is `min(100, rows × 10)`, never fewer
than `--rows`, and fewer if fewer dependents match. The default `--rows 10` scores
100 and shows 10; `--rows 50` scores 100 and shows 50. With `--rows` ≥ 100, or when
at most `--rows` dependents match, every scored dependent is shown.

Each of the four signals is log-scaled (except recency) and min-max normalized
across the pool: the pool's lowest value maps to 0, its highest to 1, and values in
between proportionally. When every repo in the pool has the same value for a signal
(including a pool of one), that signal is 0.5 for all of them. The score is the
weighted sum × 100:

| Signal | Weight |
|---|---|
| Stars | 35% |
| Forks | 25% |
| Issues + pull requests (all-time) | 20% |
| Recency of last push | 20% |

- **100** means highest in the pool on every signal. **50** means the weighted
  normalized signals average 0.5. It is not a median rank: normalization is linear
  between the pool's extremes, so one outlier can leave most of the pool far below
  50. A score of 50 is not "half as trustworthy" as 100.
- Scores are only comparable within one run. A different target, `--rows`, or
  `--min-stars` changes the pool and therefore every score.
- Because the table shows the top of the pool, scores usually cluster high: the
  defaults show only the top tenth. `--rows 100` shows the full range.
- The table footer states how many dependents were scored and how many rows are
  shown. JSON includes the per-signal 0–1 values
  under `trust.components`.

### Star-history check

```bash
dep-rank deps https://github.com/django/django --rank-by trust --trust-check
```

This optional check samples the **last 30 weeks of daily star counts for up to
25 top returned repositories**, using GitHub's authenticated
[repository star-history API](https://docs.github.com/en/rest/activity/starring#get-repository-star-history).
It adds a `concentrated_starring` caution when the window contains at least
200 stars and at least 15% arrived on one day. It does not change trust scores
or ranking. This is **not proof of fake stars**: launches and Hacker News spikes
can match the same pattern. See [StarScout][starscout] and the research references
below for the motivation and limits of star-based signals.

The table footer reports checked versus returned repositories, concentrated
patterns, `insufficient_history` (fewer than 200 stars in the window), and
`unavailable` (history could not be obtained). JSON adds top-level `trust_check`
status only when the check is requested; for example (excerpt):

```json
{
  "trust_check": {
    "complete": true,
    "window_weeks": 30,
    "repos_checked": 1,
    "insufficient_history": [],
    "unavailable": []
  },
  "repos": [{
    "trust": {
      "cautions": [{
        "code": "concentrated_starring",
        "description": "300 of 1,000 stars in the last 30 weeks arrived on 2026-09-01"
      }]
    }
  }]
}
```

`trust_check.complete` describes availability for the sampled repositories,
not coverage of all results or all historical stars. Unavailable history makes
it false and leaves the ranking usable. If trust ranking is unavailable, the
check is skipped and the status reports the returned repositories as unavailable.
The check uses no account-level signals: GitHub limited stargazer listings to
repository admins and collaborators in July 2026; aggregate star history remains
the basis of this check.

### Caution signals

Trust-ranked results may carry informational **caution signals**, built from the
same metadata fetch without extra requests by default. The optional
`--trust-check` adds a star-history caution using separate requests. Signals
appear in JSON under each repo's `trust.cautions` (a list of `code` + `description`, possibly empty) and, in
the table, as a `Cautions` column of short tags, shown only when at least one result has a signal.
A legend below the table defines each tag that appears.

| Code | Table tag | Shown when |
|---|---|---|
| `low_non_star_activity` | `low-activity` | ≥ 500 stars, and both forks and issues + pull requests are below 1% of stars |
| `stale_activity` | `stale` | ≥ 500 stars and no push for more than 365 days |
| `archived_or_disabled` | `archived` | The repository is archived or disabled |
| `new_with_high_stars` | `young` | ≥ 1,000 stars and created within the last 180 days |
| `concentrated_starring` | `spike` | Only with `--trust-check`: ≥ 200 stars in the sampled window and ≥ 15% arriving on one day |

Thresholds are fixed heuristics. Star floors stand in for a minimum sample size,
and the low-activity signal requires forks *and* issues/PRs to be low together,
because popular list and documentation repositories routinely have few issues.
Missing metadata never produces a signal. Age-based signals are measured against
the run's `scraped_at` time.

**Caution signals are not proof of fake stars or malicious behavior.** They flag
patterns worth a closer look, and legitimate repositories — finished libraries,
archived projects, a new tool that went viral — can match them. They do not
change the trust score.

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
uv run mypy src tests
```

## Acknowledgments

dep-rank is a full rewrite of [ghtopdep](https://github.com/andriyor/ghtopdep) by [Andriy Orehov](https://github.com/andriyor). The original project is licensed under MIT.

## License

MIT — see [LICENSE](LICENSE) for details.
