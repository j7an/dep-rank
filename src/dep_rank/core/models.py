"""Pydantic models for dep-rank data types."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class TrustSignals(BaseModel):
    """Raw repository metadata fetched for trust scoring.

    Transient, pre-score carrier between the GraphQL fetch and the scorer; never
    serialized (the field that holds it is excluded).
    """

    forks: int | None = None
    issues: int | None = None  # total issues (all-time)
    pull_requests: int | None = None  # total pull requests (all-time)
    pushed_at: datetime | None = None
    is_archived: bool | None = None
    is_disabled: bool | None = None
    created_at: datetime | None = None


class CautionCode(StrEnum):
    """Informational caution signals for trust-ranked repos (never a fraud verdict)."""

    LOW_NON_STAR_ACTIVITY = "low_non_star_activity"
    STALE_ACTIVITY = "stale_activity"
    ARCHIVED_OR_DISABLED = "archived_or_disabled"
    NEW_WITH_HIGH_STARS = "new_with_high_stars"
    CONCENTRATED_STARRING = "concentrated_starring"


class CautionSignal(BaseModel):
    """One caution signal with a description citing the observed values."""

    code: CautionCode
    description: str


class TrustComponents(BaseModel):
    """Pool-normalized component scores, each 0.0-1.0."""

    stars: float
    forks: float
    engagement: float  # issues + pull requests
    recency: float


class TrustScore(BaseModel):
    """Final trust score plus raw signals and the component breakdown (serialized)."""

    score: float  # 0-100, pool-relative
    forks: int | None = None
    issues: int | None = None
    pull_requests: int | None = None
    pushed_at: datetime | None = None
    components: TrustComponents
    cautions: list[CautionSignal] = []


class Repository(BaseModel):
    """A GitHub repository that depends on the target repo."""

    owner: str
    name: str
    url: str
    stars: int
    description: str | None = None
    trust_signals: TrustSignals | None = Field(default=None, exclude=True)
    trust: TrustScore | None = None


def _check_complete_matches_reason(model: ScrapeResult | DependentsResult) -> None:
    if model.complete != (model.reason is None):
        msg = f"{type(model).__name__} invariant violated: complete must equal (reason is None)"
        raise ValueError(msg)


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
    stale_pages: int = 0  # pages served from expired cache entries during this scrape

    @model_validator(mode="after")
    def _check_complete_reason_invariant(self) -> ScrapeResult:
        _check_complete_matches_reason(self)
        return self


class ScrapeSnapshot(BaseModel):
    """Running top-K handed to ``scrape_dependents``' ``on_page`` after each page."""

    top_k: list[Repository]
    pages_scraped: int
    estimated_total_pages: int
    estimated_total_dependents: int
    matched_count: int


class TrustCheckResult(BaseModel):
    """Sampled star-history check over the top results (heuristic, never a fake-star verdict)."""

    complete: bool  # True iff no checked repo is unavailable
    window_weeks: int
    repos_checked: int  # top repos the check covered (<= 25), including unavailable ones
    insufficient_history: list[str] = []  # "owner/name"
    unavailable: list[str] = []  # "owner/name"


class DependentsResult(BaseModel):
    """Result of scraping dependents for a repository."""

    source: str
    total_count: int
    filtered_count: int
    repos: list[Repository]
    dependent_type: DependentType
    scraped_at: datetime
    complete: bool = True
    reason: ScrapeReason | None = None
    pages_scraped: int = 0
    estimated_total_pages: int = 0
    ranked_by: Literal["stars", "trust"] = "stars"
    trust_check: TrustCheckResult | None = None
    # pages served from expired cache entries; excluded from JSON
    stale_pages: int = Field(default=0, exclude=True)
    # False when trust scores used partial metadata; excluded from JSON
    trust_metadata_complete: bool = Field(default=True, exclude=True)

    @model_validator(mode="after")
    def _check_complete_reason_invariant(self) -> DependentsResult:
        _check_complete_matches_reason(self)
        return self


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


class TrustMetadataResult(BaseModel):
    """Return type of the trust-metadata fetch, carrying fetch status.

    ``failed=True`` means no usable metadata was obtained (a 401, or every batch
    errored) and the caller should fall back to star ranking. ``complete`` is True
    only when every requested repo received signals.
    """

    repos: list[Repository]
    failed: bool
    complete: bool
