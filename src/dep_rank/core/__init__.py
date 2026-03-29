"""Core library for dep-rank. No CLI or MCP dependencies."""

from dep_rank.core.cache import SqliteCache
from dep_rank.core.graphql import enrich_with_graphql
from dep_rank.core.models import (
    CodeSearchHit,
    CodeSearchResult,
    DependentsResult,
    DependentType,
    Repository,
)
from dep_rank.core.rate_limiter import TokenBucketRateLimiter
from dep_rank.core.scraper import scrape_dependents
from dep_rank.core.search import search_code
from dep_rank.core.validation import validate_github_url

__all__ = [
    "CodeSearchHit",
    "CodeSearchResult",
    "DependentType",
    "DependentsResult",
    "Repository",
    "SqliteCache",
    "TokenBucketRateLimiter",
    "enrich_with_graphql",
    "scrape_dependents",
    "search_code",
    "validate_github_url",
]
