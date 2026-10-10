"""Display-only package mapping heuristics, never a quality or authenticity verdict."""

from __future__ import annotations

import re

from dep_rank.core.models import PackageDownloads

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
