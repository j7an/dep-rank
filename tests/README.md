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
- When asserting that **no** request was made, assert `mock_http.request_count == 0`. An
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
