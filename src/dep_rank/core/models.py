"""Pydantic models for dep-rank data types."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class DependentType(StrEnum):
    """Type of dependent to search for."""

    REPOSITORY = "REPOSITORY"
    PACKAGE = "PACKAGE"


class Repository(BaseModel):
    """A GitHub repository that depends on the target repo."""

    owner: str
    name: str
    url: str
    stars: int
    description: str | None = None


class ScrapeResult(BaseModel):
    """Result of scraping dependents, including progress metadata."""

    repos: list[Repository]
    pages_scraped: int
    max_pages: int
    estimated_total_pages: int
    estimated_total_dependents: int


class DependentsResult(BaseModel):
    """Result of scraping dependents for a repository."""

    source: str
    total_count: int
    filtered_count: int
    repos: list[Repository]
    dependent_type: DependentType
    scraped_at: datetime


class CodeSearchHit(BaseModel):
    """A single code search match within a dependent repository."""

    repo: Repository
    file_url: str
    file_path: str
    matches: int


class CodeSearchResult(BaseModel):
    """Result of searching code across dependent repositories."""

    source: str
    query: str
    hits: list[CodeSearchHit]
    searched_repos: int
