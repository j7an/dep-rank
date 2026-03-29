# dep-rank

Rank GitHub dependents by stars.

[![PyPI](https://img.shields.io/pypi/v/dep-rank)](https://pypi.org/project/dep-rank/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

dep-rank finds the most popular repositories that depend on a given GitHub project. It scrapes GitHub's dependents page, enriches results via the GraphQL API, and works as both a CLI tool and an MCP server for LLM assistants.

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

### `dep-rank mcp` — Start MCP server

```bash
dep-rank mcp                                # STDIO (default)
dep-rank mcp --transport http --port 8000   # HTTP
```

### `dep-rank cache` — Manage cache

```bash
dep-rank cache stats    # Show cache size
dep-rank cache clear    # Clear all cached data
```

## MCP Server

dep-rank includes an MCP server for use with Claude Desktop, Claude Code, Cursor, and other MCP-compatible tools.

### Tools

| Tool | Description | Requires Token |
|------|-------------|---------------|
| `get_top_dependents` | List top dependents by stars | No |
| `get_dependent_details` | Enrich with descriptions via GraphQL | Yes |
| `search_dependent_code` | Search code across dependents | Yes |

### Prompts

| Prompt | Description |
|--------|-------------|
| `analyze_ecosystem` | Guided workflow for full ecosystem analysis |
| `find_usage_patterns` | Find how dependents use a specific API |

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dep-rank": {
      "command": "uvx",
      "args": ["dep-rank-mcp"],
      "env": {
        "DEP_RANK_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### Claude Code

Add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "dep-rank": {
      "command": "uvx",
      "args": ["dep-rank-mcp"],
      "env": {
        "DEP_RANK_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### HTTP Deployment

```bash
DEP_RANK_TOKEN=ghp_xxx dep-rank mcp --transport http --port 8000
```

## Authentication

Set the `DEP_RANK_TOKEN` environment variable with a GitHub personal access token:

```bash
export DEP_RANK_TOKEN=ghp_your_token_here
```

**What works without a token:**
- `dep-rank deps` — core scraping and star ranking

**What requires a token:**
- `--descriptions` flag — fetches repo descriptions via GitHub GraphQL API
- `dep-rank search` — code search across dependents
- MCP tools: `get_dependent_details`, `search_dependent_code`

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens) with `public_repo` scope.

## How It Works

dep-rank uses a three-stage pipeline:

1. **Scrape** — fetches GitHub's `/network/dependents` HTML pages to discover all dependents and their approximate star counts
2. **Enrich** (optional) — one GraphQL batch query fetches accurate star counts and descriptions for the top N results (replaces 100 individual REST API calls)
3. **Present** — returns structured results as a Rich table (CLI) or Pydantic model (MCP)

Responses are cached in a local SQLite database (`~/.cache/dep-rank/`) with ETag support for conditional requests.

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
