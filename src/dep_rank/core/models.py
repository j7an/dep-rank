"""Pydantic models for dep-rank data types."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator


class DependentType(StrEnum):
    """Type of dependent to search for."""

    REPOSITORY = "REPOSITORY"
    PACKAGE = "PACKAGE"


class ScrapeReason(StrEnum):
    """Why a scrape terminated short of exhausting all pages.

    ``None`` (absence of a reason) means the scrape was complete. The invariant
    ``complete == (reason is None)`` holds across ScrapeResult and DependentsResult.
    """

    MAX_PAGES_REACHED = "max_pages_reached"
    TREND_CONVERGED = "trend_converged"
    NETWORK_FAILURE = "network_failure"
    RATE_LIMITED = "rate_limited"


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
    complete: bool = True
    reason: ScrapeReason | None = None
    matched_count: int = 0

    @model_validator(mode="after")
    def _check_complete_reason_invariant(self) -> ScrapeResult:
        if self.complete != (self.reason is None):
            msg = "ScrapeResult invariant violated: complete must equal (reason is None)"
            raise ValueError(msg)
        return self


class ScrapeSnapshot(BaseModel):
    """One emission from the streaming scraper.

    Per-page emissions have ``done=False``; exactly one terminal emission has
    ``done=True`` and carries the authoritative ``complete``/``reason``.
    """

    top_k: list[Repository]
    pages_scraped: int
    estimated_total_pages: int
    estimated_total_dependents: int
    matched_count: int
    done: bool = False
    complete: bool = False
    reason: ScrapeReason | None = None


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
