"""Display-only package mapping heuristics, never a quality or authenticity verdict."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx2

from dep_rank import __version__
from dep_rank.core.models import DownloadsCheckResult, PackageDownloads, Repository
from dep_rank.core.scraper import REQUEST_TIMEOUT

logger = logging.getLogger(__name__)
LOOKUP_URL = "https://packages.ecosyste.ms/api/v1/packages/lookup"
_LOOKUP_ECOSYSTEMS = {"go", "npm", "pypi", "cargo", "maven", "nuget", "rubygems"}

ATTESTATION_KINDS = {
    "PYPI_PUBLISH_ATTESTATION",
    "SLSA_ATTESTATION",
    "RUBYGEMS_PUBLISH_ATTESTATION",
}
_SUFFIXES = (".js", "-js", "js", ".py", "-py", "-python")
_PREFIXES = ("py", "python-", "node-")


def norm(text: str) -> str:
    """Collapse separators and case for heuristic name comparisons."""
    return re.sub(r"[-_.]", "", text.lower())


def derived(text: str) -> set[str]:
    """Include the normalized name and each single-affix variant."""
    # ponytail: sentry-sdk-type misses need a manifest reverse-check if reported.
    lowered = text.lower()
    names = {norm(lowered)}
    for suffix in _SUFFIXES:
        if lowered.endswith(suffix):
            names.add(norm(lowered[: -len(suffix)]))
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            names.add(norm(lowered[len(prefix) :]))
    names.discard("")
    return names


def package_key(ecosystem: str, name: str) -> tuple[str, str]:
    """Return the registry identity used for comparison and gap-fill queries."""
    ecosystem = ecosystem.lower()
    if ecosystem == "pypi":
        name = re.sub(r"[-_.]+", "-", name).lower()
    elif ecosystem == "nuget":
        name = name.lower()
    return ecosystem, name


def parse_attested(payload: object) -> set[tuple[str, str]]:
    """Extract identities supported by a literally verified publish attestation."""
    if not isinstance(payload, dict):
        raise ValueError("Package versions payload must be an object")
    versions = payload.get("versions", [])
    if not isinstance(versions, list):
        raise ValueError("Package versions must be a list")
    attested: set[tuple[str, str]] = set()
    for entry in versions:
        if not isinstance(entry, dict):
            continue
        provenance = entry.get("relationProvenance")
        if not isinstance(provenance, str) or provenance not in ATTESTATION_KINDS:
            continue
        attestations = entry.get("attestations")
        if not isinstance(attestations, list) or not any(
            isinstance(attestation, dict) and attestation.get("verified") is True
            for attestation in attestations
        ):
            continue
        key = entry.get("versionKey")
        if not isinstance(key, dict):
            continue
        system, name = key.get("system"), key.get("name")
        if isinstance(system, str) and isinstance(name, str):
            attested.add(package_key(system, name))
    return attested


def pick_package(
    candidates: list[object],
    *,
    owner: str,
    repo: str,
    attested: set[tuple[str, str]],
) -> PackageDownloads | None:
    """Pick the most-downloaded accepted package, retaining the first on ties."""
    # ponytail: periods are compared raw; prefer last-month if a mis-pick is reported.
    owner_names, repo_names = derived(owner), derived(repo)
    winner: PackageDownloads | None = None
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("status") == "removed":
            continue
        name, ecosystem = candidate.get("name"), candidate.get("ecosystem")
        downloads, period = candidate.get("downloads"), candidate.get("downloads_period")
        if (
            not isinstance(name, str)
            or not isinstance(ecosystem, str)
            or not isinstance(downloads, int)
            or isinstance(downloads, bool)
            or not isinstance(period, str)
        ):
            continue
        verified = package_key(ecosystem, name) in attested
        if name.startswith("@"):
            scope, separator, rest = name[1:].partition("/")
            name_match = bool(separator and rest and norm(scope) in owner_names)
        else:
            name_match = bool(norm(name)) and norm(name) in repo_names
        if not (verified or name_match):
            continue
        if winner is None or downloads > winner.downloads:
            winner = PackageDownloads(
                ecosystem=ecosystem,
                name=name,
                downloads=downloads,
                period=period,
                verified=verified,
            )
    return winner


class _ServiceUnavailableError(Exception):
    """A service failure that ends the remaining pass."""


async def _request_downloads(
    session: httpx2.AsyncClient,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    allow_not_found: bool = False,
) -> httpx2.Response:
    request = session.build_request(
        "GET",
        url,
        params=params,
        headers={"User-Agent": f"dep-rank/{__version__}"},
        timeout=REQUEST_TIMEOUT,
    )
    request.headers.pop("Authorization", None)
    request.headers.pop("Cookie", None)
    response = await session.send(request, auth=None, follow_redirects=False)
    status = response.status_code
    if status == 429 or status >= 500:
        raise _ServiceUnavailableError(f"HTTP {status} from {url}")
    if status != 200 and not (allow_not_found and status == 404):
        raise ValueError(f"HTTP {status} from {url}")
    return response


async def _lookup_packages(
    session: httpx2.AsyncClient, params: dict[str, str | int]
) -> list[object]:
    response = await _request_downloads(session, LOOKUP_URL, params=params)
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Package lookup payload must be a list")
    return payload


async def fetch_downloads(
    session: httpx2.AsyncClient, repos: list[Repository]
) -> tuple[list[Repository], DownloadsCheckResult]:
    """Fetch display-only downloads without forwarding caller credentials."""
    updated = [repo.model_copy(update={"downloads": None}) for repo in repos]
    unavailable: list[str] = []
    # ponytail: add SqliteCache storage if repeat-run latency is reported.
    # ponytail: add both services to the drift_check canary if schema drift breaks this unnoticed.
    # ponytail: add a bounded gather if --rows > 25 is reported slow.
    for index, repo in enumerate(repos):
        full_name = f"{repo.owner}/{repo.name}"
        try:
            # ponytail: per_page=50 assumes the real package ranks in the top 50.
            candidates = await _lookup_packages(
                session,
                {
                    "repository_url": f"https://github.com/{full_name}",
                    "sort": "downloads",
                    "order": "desc",
                    "per_page": 50,
                },
            )
            project = quote(f"github.com/{full_name}", safe="")
            response = await _request_downloads(
                session,
                f"https://api.deps.dev/v3/projects/{project}:packageversions",
                allow_not_found=True,
            )
            attested = set() if response.status_code == 404 else parse_attested(response.json())
            present = {
                package_key(candidate["ecosystem"], candidate["name"])
                for candidate in candidates
                if isinstance(candidate, dict)
                and isinstance(candidate.get("ecosystem"), str)
                and isinstance(candidate.get("name"), str)
            }
            for ecosystem, name in sorted(attested - present):
                if ecosystem in _LOOKUP_ECOSYSTEMS:
                    candidates.extend(
                        await _lookup_packages(
                            session,
                            {
                                "ecosystem": ecosystem,
                                "name": name,
                            },
                        )
                    )
        except (httpx2.RequestError, _ServiceUnavailableError) as exc:
            logger.debug("Downloads unavailable for %s: %s", full_name, exc)
            unavailable.extend(f"{row.owner}/{row.name}" for row in repos[index:])
            # ponytail: add one 429/5xx retry if flaky services make unavailable common.
            break
        except ValueError as exc:
            logger.debug("Downloads unavailable for %s: %s", full_name, exc)
            unavailable.append(full_name)
            continue
        updated[index] = repo.model_copy(
            update={
                "downloads": pick_package(
                    candidates,
                    owner=repo.owner,
                    repo=repo.name,
                    attested=attested,
                )
            }
        )
    return updated, DownloadsCheckResult(complete=not unavailable, unavailable=unavailable)
