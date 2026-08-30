# Project Instructions

This file defines repository-specific guidance for contributors and automated
coding tools. Keep it portable: describe project facts, invariants, commands,
and required outcomes without depending on a particular execution environment.

## Repository Purpose

`dep-rank` is a Python 3.11+ command-line application that discovers GitHub
repository dependents, ranks them by stars or an optional trust heuristic, and
can search code across the discovered repositories. It combines HTML scraping,
optional GraphQL enrichment, bounded concurrency, rate limiting, and a local
SQLite cache.

The supported Python matrix is 3.11, 3.12, and 3.13. The package uses a
`src/` layout, `uv` for environments and dependency locking, Hatchling for
builds, and hatch-vcs for versions derived from Git tags.

## Architecture and Module Responsibilities

- `src/dep_rank/cli/app.py` owns the Click command surface: `deps`, `search`,
  `cache`, and `--version`.
- `src/dep_rank/cli/formatters.py` owns Rich tables, JSON presentation, and
  scrape-summary formatting. Keep presentation concerns out of core modules.
- `src/dep_rank/core/validation.py` parses and validates GitHub repository URLs.
- `src/dep_rank/core/scraper.py` owns dependents-page parsing, pagination,
  streaming aggregation, adaptive stopping, partial-result state, and
  stale-while-revalidate coordination.
- `src/dep_rank/core/rate_limiter.py` owns token-bucket request budgets, 429
  backoff, and advisory AIMD concurrency state shared by foreground and
  background work.
- `src/dep_rank/core/cache.py` owns SQLite persistence, expiry, ETags, and cache
  lifecycle behavior.
- `src/dep_rank/core/graphql.py` owns batched metadata enrichment through the
  GitHub GraphQL API.
- `src/dep_rank/core/trust.py` owns the pool-relative trust-ranking heuristic.
- `src/dep_rank/core/search.py` owns code search over the bounded dependent set.
- `src/dep_rank/core/models.py` owns Pydantic result and repository models.
- `src/dep_rank/scripts/drift_check.py` supports the scheduled scraper-drift
  canary.
- `scripts/derive-published-version.sh` and `scripts/classify-prerelease.sh`
  are release guards used by `.github/workflows/release.yml`.

## Setup and Commands

```bash
# Create or synchronize the development environment
uv sync

# Run the CLI from the checkout
uv run dep-rank --help

# Run all tests with configured branch coverage and reports
uv run pytest

# Run fast correctness checks without coverage output
uv run pytest --no-cov -q

# Run one file or one test
uv run pytest tests/core/test_validation.py -v
uv run pytest tests/core/test_validation.py::TestValidateGithubUrl -v

# Lint, format-check, and type-check the same paths used by CI
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src tests

# Run all configured local hooks
uv run pre-commit run --all-files

# Validate GitHub Actions changes
actionlint .github/workflows/*.yml
zizmor --offline .

# Reproduce the PR changed-line coverage gate after generating coverage.xml
uv run diff-cover coverage.xml --fail-under=80 --compare-branch=origin/main
```

Run targeted tests after each code change. Before opening or updating a pull
request, run the checks that cover every changed area and then the full suite.

## Python and Source Conventions

- Target Python 3.11 syntax and behavior. Use built-in generic types and `X | None`
  unions; preserve `from __future__ import annotations` where present.
- Ruff owns formatting and lint policy. The configured line length is 100.
- Mypy runs in strict mode for `src/dep_rank/`; keep public and internal
  interfaces fully typed.
- Prefer focused functions, explicit state transitions, and descriptive names.
- Keep imports sorted as standard library, third-party, then local modules.
- Do not block the event loop with synchronous network or file operations in
  async request paths.
- Keep CLI formatting in `cli/` and reusable behavior in `core/`.
- Do not edit `src/dep_rank/_version.py`; hatch-vcs generates it during builds.
- Do not restore configuration to `setup.cfg`; it remains only for legacy
  flake8 compatibility. Active Ruff, mypy, build, and coverage settings live in
  `pyproject.toml`.

## Testing and Coverage

- Tests mirror the source layout under `tests/cli/` and `tests/core/`.
- `asyncio_mode = auto` is configured; async tests do not need explicit asyncio
  markers.
- Unit and CLI tests must not depend on live GitHub state. Use `aioresponses`,
  fixtures, or mocks at HTTP/process boundaries.
- The autouse `clean_env` fixture removes `DEP_RANK_TOKEN` for every test. Do not
  bypass it or allow credentials from a developer shell to affect results.
- Reuse fixtures from `tests/conftest.py` for dependents-page HTML and common
  environment state.
- Overall coverage requires at least 90% with branch coverage enabled. Pull
  requests also require at least 80% coverage on changed lines.
- Add regression tests for fixes and boundary-focused tests for pagination,
  concurrency, rate limiting, partial results, caching, and CLI validation.
- Keep tests deterministic across Linux, macOS, Windows, and Python 3.11-3.13.

## Scraper and Runtime Invariants

- Scraping is bounded by `max_pages`; never introduce an unbounded pagination or
  retry loop.
- A scrape result must report whether it is complete and why it stopped. When
  incomplete, counts are lower bounds over pages actually processed.
- Preserve the distinction between exhaustion, `max_pages_reached`,
  `trend_converged`, `network_failure`, and `rate_limited` outcomes.
- `search` uses a bounded non-adaptive top-K pre-pass. Do not silently enable
  adaptive stopping for that command.
- Unauthenticated scraping has a much smaller request budget. Background cache
  refresh must not compete with its foreground walk.
- Stale-while-revalidate refreshes are deduplicated, capped, share limiter state,
  reserve foreground token headroom, respect AIMD suppression, and drain before
  their HTTP session closes.
- Feed foreground and background 429 responses into the shared limiter so
  backoff and concurrency recovery remain coordinated.
- Preserve ETag-based conditional requests and SQLite cache lifecycle rules.
- Trust scores are pool-relative heuristics, not absolute quality or fraud
  determinations. Keep that limitation visible in APIs, output, and docs.

## Dependencies and Lockfile Policy

- `uv.lock` is authoritative and must remain synchronized with `pyproject.toml`.
- Use `uv add`, `uv remove`, or `uv lock` rather than hand-editing lockfile
  content.
- Ask before adding a new runtime or development dependency.
- The global `tool.uv.exclude-newer` cooldown is one week. Temporary
  `exclude-newer-package` entries exist to allow security-fixed releases through
  the cooldown; remove an override only after its advisory fix is older than the
  global window.
- Dependabot groups Python and GitHub Actions updates. Its CI repair job may
  regenerate `uv.lock`; do not weaken the loud-fail guard when repair fails.
- Dependency Review blocks moderate-or-higher vulnerabilities and disallowed
  licenses, subject only to documented repository exceptions.

## CI and Workflow Policy

- `.github/workflows/ci.yml` owns Ruff, mypy, the 3x3 OS/Python pytest matrix,
  changed-line coverage, lockfile repair, and dependency review.
- `.github/workflows/security.yml` delegates CodeQL, secret scanning, OSV,
  Trivy, and workflow analysis to the shared security workflow.
- `dependency-safety.yml` handles Dependabot analysis. The status-only
  `dependency-safety-non-bot-gate.yml` supplies the required
  `dependency-safety / gate` context for non-Dependabot pull requests, including
  fork pull requests. Preserve this split.
- The shared-workflows caller set is exactly dependency safety, its non-bot gate,
  pre-commit autoupdate, security scan, and tag release. Keep the set and all
  callers on one uniform release.
- External GitHub Actions and cross-repository reusable workflows must be pinned
  to immutable lowercase 40-character commit SHAs with trailing `# vX.Y.Z`
  comments. Dereference tags to commit SHAs; never use a mutable tag as the
  executable ref.
- Workflow contract tests must assert semantic policy: expected target, exact
  caller set, full-length SHA shape, version-comment shape, and dynamic
  uniformity. Never snapshot the current SHA/version pair in test source.
- `tests/test_shared_workflows_contract.py` enforces both the semantic caller
  contract and the no-literal-snapshot source policy. Construct detector
  examples at runtime rather than embedding literal pin triples.
- Keep workflow permissions minimal and explicit. Do not add write permissions
  without tracing the job's actual API operations.

## Release Policy

- `RELEASE.md` is the operational source of truth for stable releases,
  prereleases, credential topology, recovery, and post-release checks.
- PyPI and TestPyPI trusted-publishing jobs must remain caller-owned in
  `.github/workflows/release.yml`. Do not move publishing into a cross-repository
  reusable workflow unless PyPI explicitly supports that trusted-publisher
  identity model.
- Preserve the release chain: CI gate, build, TestPyPI publish, TestPyPI install
  verification, production PyPI approval/publish, then GitHub Release.
- Keep TestPyPI verification on an explicit Python version and an ephemeral
  `.verify/pyproject.toml` whose explicit TestPyPI source applies only to the
  package under test. Do not replace it with a broad extra-index fallback.
- The TestPyPI verifier intentionally disables setup-uv caching and tolerates an
  empty pre-checkout workdir. Do not re-enable caching without first providing
  real dependency files for the cache key.
- Release tags are lightweight refs created by the shared tag-release workflow.
  Keep built-version/tag checks and main-ancestry verification as hard gates.
- Use Conventional Commit subjects because release automation derives semantic
  version bumps from commit history.

## Git, Documentation, and Pull Requests

- Never commit directly to `main` unless explicitly directed. Create a branch or
  isolated worktree from current `origin/main` for issue, feature, or fix work.
- Preserve unrelated changes in dirty checkouts and linked worktrees.
- Stage explicit paths; do not use broad staging commands that can capture
  unrelated files.
- Ask before destructive Git operations, adding dependencies, or expanding scope
  beyond the requested change.
- Do not commit working specs, implementation plans, or `HANDOFF.md` unless
  explicitly requested.
- Use Conventional Commit subjects that describe the actual change.
- Update `README.md` for user-visible CLI or behavior changes, `RELEASE.md` for
  release-process changes, and `tests/README.md` when test organization or
  coverage commands change.
- Prefer `rg` and `rg --files` for text and file discovery. Use
  `ast-grep --lang <language> -p '<pattern>'` when the search depends on syntax or
  code structure.
- Before handoff, inspect the exact diff, run relevant checks, confirm no stale
  references remain, and report any pre-existing warnings separately from new
  failures.
- Pull requests should explain the behavioral or policy change, list validation
  performed, link relevant upstream changes or issues, and keep unrelated work
  out of the diff.
