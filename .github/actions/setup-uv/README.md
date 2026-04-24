# setup-uv composite action

Installs uv with dependency caching and runs `uv sync --locked` to install
the project environment. Used by jobs that need a synced dev environment
to run tests, linters, type-checkers, or `uv build`.

## Inputs

| Name             | Default | Description                          |
| ---------------- | ------- | ------------------------------------ |
| `python-version` | `3.11`  | Python version to install and use.   |

## When NOT to use this action

Skip this action and call `astral-sh/setup-uv` inline for jobs that:

- Mutate the lockfile (`uv lock`) — `--locked` would fail on drift.
- Don't need the project environment synced (e.g., `pre-commit autoupdate`).
