# Test Suite

Tests mirror the `src/dep_rank/` layout. The authoritative list of tests is
`uv run pytest -o addopts='' --collect-only -q` (the configured `-v` in `addopts`
cancels `-q`, so clear it when you only want test IDs).

## Running tests

```bash
uv run pytest                                   # full suite with coverage reports
uv run pytest --no-cov -q                       # fast run without coverage
uv run pytest tests/core/test_validation.py -v  # one file
uv run pytest tests/core/test_validation.py::TestValidateGithubUrl -v  # one class
```

## Layout

### `tests/cli/` — CLI surface

- **`conftest.py`**: autouse fixture pointing `dep_rank.cli.app._cache_dir` at a
  per-test temporary directory, so CLI tests use a real SQLite cache without
  touching the developer's cache.
- **`test_commands.py`**: `deps`, `search`, `cache`, and `--version` through
  Click's `CliRunner` — argument and token validation, table/JSON output, trust
  ranking and its fallbacks, live progress, the cache directory resolver, and the
  subprocess checks that need the real logging setup.
- **`test_formatters.py`**: Rich tables and JSON output — `humanize`, the
  dependents table (star and trust layouts, cautions, footer), search results,
  scrape summaries, and partial-result warnings.

### `tests/core/` — library logic

- **`test_dependents.py`**: `get_dependents` input validation, the default
  star-ranked call, and importing it loads no CLI dependencies.
- **`test_validation.py`**: `validate_github_url` accepted forms and error messages.
- **`test_models.py`**: model behavior the project defines — the
  `complete == (reason is None)` invariant, defaults, enum wire values, and JSON
  field exclusions.
- **`test_scraper.py`**: page parsing, the `scrape_dependents` walk, retries,
  `Retry-After` and backoff handling, and cache hits.
- **`test_scraper_streaming.py`**: top-K aggregation, dedup, the `on_page`
  callback, and stop reasons.
- **`test_scraper_adaptive_stop.py`**: the adaptive-stop heuristic.
- **`test_cache.py`**: SQLite get/put/expiry/clear/stats and the uninitialized-cache error.
- **`test_cache_swr.py`**: stale-while-revalidate refreshes — dedup, cooldown,
  429 pause, foreground headroom, conditional `If-None-Match` requests, and drain.
- **`test_rate_limiter.py`**: token bucket, 429 backoff, and the background pause.
- **`test_graphql.py`**: trust-metadata query construction and enrichment,
  including batches, partial responses, and failure fallbacks.
- **`test_trust.py`**: the pool-relative trust score and caution signals.
- **`test_star_history.py`**: the sampled star-history trust check.
- **`test_search.py`**: `search_code` over the bounded dependent set.
- **`test_drift_check.py`**: the drift canary's evaluation and exit codes.

### `tests/test_shared_workflows_contract.py`

Semantic checks on GitHub workflows: the shared-workflow caller set, SHA pin
shape and uniformity, and the release policy invariants from `AGENTS.md`.

## Shared fixtures (`tests/conftest.py`)

Request these by name as test parameters:

- **`mock_http`** yields a `FakeHTTP` response queue wired through
  `httpx2.MockTransport`; **`session`** yields an `httpx2.AsyncClient` using it.
- **`cache`** initializes a temporary `SqliteCache` and closes it after the test.
- **`clean_env`** (autouse) removes `DEP_RANK_TOKEN` with `monkeypatch`.

## Shared helpers (`tests/conftest.py`)

Import these with `from tests.conftest import ...`:

- **`dependents_page`** builds dependents HTML with repository rows, pagination,
  and repository/package counts. The `DEPENDENTS_HTML_*` constants are built with
  it and imported by scraper and CLI tests.
- **`fast_limiter`** returns a high-budget rate limiter for multi-page walks.
- **`make_repo`** builds a `Repository` with the standard GitHub URL.

## Writing tests

- Mock HTTP with `FakeHTTP` (the `mock_http` fixture), never live GitHub.
- When asserting that **no** request was made, count `mock_http.requests`. An
  unregistered URL raises `httpx2.ConnectError`, which the scraper
  retries with backoff and the background refresher swallows, so an assertion
  that relies on that error passes for the wrong reason or fails slowly. To make
  an unexpected request fail at once, register it with
  `exception=AssertionError(...)`.
- Prefer parametrized tests for input tables, and keep every case's assertions.
- Remove a test only when another test fails under the same source mutation.

## Coverage

`uv run pytest` measures branch coverage and writes terminal, `htmlcov/`, and
`coverage.xml` reports; the settings live in `pyproject.toml`
(`[tool.pytest.ini_options] addopts` and `[tool.coverage.*]`).

On pull requests, the CI `coverage` job enforces at least 90% total branch
coverage and at least 80% on changed lines of `src/dep_rank/` (excluding the
generated `_version.py`). The `test` matrix runs with `--no-cov`. To check the
changed-line gate locally:

```bash
uv run pytest
uv run diff-cover coverage.xml --fail-under=80 --compare-branch=origin/main
```
