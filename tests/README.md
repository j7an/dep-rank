# Test Suite Documentation

## Overview

This test suite provides comprehensive coverage of the dep-rank codebase. The tests are organized into logical modules covering unit tests, integration tests, and CLI functionality tests.

## Running Tests

### Run all tests
```bash
uv run pytest tests/ -v
```

### Run with coverage report
```bash
uv run pytest tests/
```
Coverage runs automatically (see [Running Coverage Report](#running-coverage-report) below).

### Run specific test file
```bash
uv run pytest tests/core/test_validation.py -v
```

### Run specific test class
```bash
uv run pytest tests/core/test_validation.py::TestValidateGithubUrl -v
```

## Test Structure

Tests are organized into three packages mirroring the `src/dep_rank/` layout. The authoritative test count and per-module breakdown is `uv run pytest tests/ --collect-only -q`.

### `tests/cli/` — CLI surface

- **`test_commands.py`**: Click command invocations for `deps`, `search`, `mcp`, `cache`, and `--version`. Covers help output, missing-token errors, table/JSON modes, and full-flow runs against mocked HTTP layers.
- **`test_formatters.py`**: Pure formatter helpers — `humanize` star formatting, `print_dependents_table`, `print_search_results`, and `format_scrape_summary`.

### `tests/core/` — Pure logic

- **`test_validation.py`**: `validate_github_url` URL parsing and validation (https/http/www, trailing slashes, invalid characters, bare `owner/repo`, empty/whitespace edges).
- **`test_models.py`**: Pydantic models — `Repository`, `DependentType`, `DependentsResult`, `ScrapeResult`, `CodeSearchResult` — including JSON round-trips.
- **`test_scraper.py`**: HTML parsing (`parse_dependent_counts`, `parse_dependents_page`) and the async `scrape_dependents` flow including pagination, dedup, min-stars filtering, error/304 handling, cache integration, and progress callbacks.
- **`test_graphql.py`**: GraphQL batch query construction and `enrich_with_graphql` star/description enrichment, including the 100-node batch boundary and fallback paths.
- **`test_cache.py`**: SQLite cache get/put/expiry/clear/stats and the uninitialized-cache error contract.
- **`test_rate_limiter.py`**: Token-bucket rate limiter — within-limit allow, over-limit block, and replenishment over time.
- **`test_search.py`**: `search_code` over multiple repos with progress callbacks, max-repos limit, and non-200 / client-error skip behavior.

### `tests/mcp/` — MCP server surface

- **`test_lifecycle.py`**: MCP server lifecycle (server starts cleanly).
- **`test_prompts.py`**: Prompt registration for `analyze_ecosystem` and `find_usage_patterns`.
- **`test_tools.py`**: MCP tool implementations (`get_top_dependents`, `get_dependent_details`, `search_dependent_code`), prompt body content, and tool annotations. Includes token-required error contracts and cached-state behavior.

## Test Fixtures

### Defined in `tests/conftest.py`

- **`clean_env`** (autouse): Snapshots `DEP_RANK_TOKEN` before each test and restores it after, so tests that set or unset the token do not leak state across the suite.

Module-level HTML constants are also defined in `conftest.py` and imported directly by `tests/core/test_scraper.py`:

- `DEPENDENTS_HTML_PAGE_1`: dependents page with three repos and a "Next" link.
- `DEPENDENTS_HTML_LAST_PAGE`: terminal page with one repo and a "Previous" link.
- `DEPENDENTS_HTML_NO_RESULTS`: empty dependents page (zero repositories).
- `DEPENDENTS_HTML_WITH_COUNTS_PAGE_1`: page 1 with both "Repositories" and "Packages" tab counts visible.
- `DEPENDENTS_HTML_WITH_COUNTS`: terminal page with both tab counts.

## Coverage Report

Run `uv run pytest` to generate the current coverage report. With `[tool.coverage.run] branch = true` enabled, the terminal output includes branch columns (`Branch` and `BrPart`) in addition to missing lines. The HTML report is written to `htmlcov/index.html`; `coverage.xml` is also written for `diff-cover` consumption (used by CI on PRs and available locally for the same gate). Coverage activation, configuration file pointer, and report formats are all configured in `pytest.ini`'s `addopts`; measurement policy (source, branch mode, omit, threshold, exclusions) is owned by `pyproject.toml` `[tool.coverage.*]`.

## Running Coverage Report

```bash
uv run pytest tests/
open htmlcov/index.html
```

Coverage runs automatically because `pytest.ini`'s `addopts` includes `--cov`, `--cov-config=pyproject.toml`, `--cov-report=html`, `--cov-report=term-missing`, and `--cov-report=xml`. Branch coverage is enabled by `pyproject.toml` `[tool.coverage.run] branch = true`, so no extra `--cov-branch` flag is needed after this change.

## Running Diff Coverage Locally

CI enforces a minimum coverage threshold on changed lines via [`diff-cover`](https://github.com/Bachmann1234/diff_cover). The gate runs on PRs only, on a single matrix cell (`ubuntu-latest` + Python 3.11), with `--fail-under=80`. To check the same gate locally before pushing:

```bash
uv run pytest                                              # produces coverage.xml
uv run diff-cover coverage.xml \
  --fail-under=80 \
  --compare-branch=origin/main
```

`diff-cover` reads `coverage.xml`, computes the git diff between `HEAD` and `origin/main`, and reports the percentage of changed lines covered by tests. If your branch is based off a base other than `main`, replace `origin/main` accordingly.

The local gate uses the same threshold and inputs as CI. A PR that passes locally should pass in CI; if they disagree, the most likely cause is stale local state — re-run `git fetch origin` and re-check `coverage.xml` is current.

## Best Practices

1. **Use fixtures**: Reuse common test data via pytest fixtures
2. **Mock external services**: Use `unittest.mock` to isolate units from external dependencies
3. **Organize by feature**: Group related tests into test classes
4. **Clear naming**: Use descriptive test names that explain what is being tested
5. **Test edge cases**: Include boundary conditions and error scenarios

## Adding New Tests

When adding new tests:

1. Identify which module the test belongs to (or create new module)
2. Create a test class for logical grouping
3. Use descriptive test names starting with `test_`
4. Add docstrings explaining what the test verifies
5. Use existing fixtures or create new ones as needed
6. Ensure test is isolated and doesn't depend on external state

Example:
```python
class TestMyFeature:
    """Tests for my new feature."""

    def test_basic_functionality(self):
        """Test that basic functionality works."""
        result = my_function(test_input)
        assert result == expected_output
```

## Continuous Integration

These tests run with a minimum 90% coverage enforcement. Configuration is split between `pytest.ini` (activation and report formatting) and `pyproject.toml` `[tool.coverage.*]` (measurement policy). Specifically:

- Coverage minimum threshold: 90% overall with branch coverage enabled (via `[tool.coverage.run] branch = true` and `[tool.coverage.report] fail_under`)
- Coverage report formats: html, term-missing, xml (the terminal report now includes `Branch` / `BrPart`; xml still feeds the PR-only `diff-cover` gate at `--fail-under=80`)
- Warnings filtered appropriately

Failed tests, overall coverage (branch-enabled total) below 90%, or diff coverage below 80% on changed lines (PRs only, on the `ubuntu-latest`/Python 3.11 cell) will cause CI to fail.
