"""Behavioral tests for display-only package matching."""

from __future__ import annotations

import pytest

from dep_rank.core.downloads import derived, norm, package_key, parse_attested, pick_package


def pkg(
    name: object,
    downloads: object,
    ecosystem: object = "npm",
    period: object = "last-month",
    **extra: object,
) -> dict[str, object]:
    return {
        "ecosystem": ecosystem,
        "name": name,
        "downloads": downloads,
        "downloads_period": period,
        **extra,
    }


@pytest.mark.parametrize(
    ("text", "member"),
    [
        ("aws-cli", "awscli"),
        ("next.js", "next"),
        ("pytorch", "torch"),
        ("redis-py", "redis"),
        ("vuejs", "vue"),
        ("Pillow", "pillow"),
        ("node-fetch", "fetch"),
        ("python-dateutil", "dateutil"),
        ("scikit-learn", "scikitlearn"),
        ("next-js", "next"),
        ("redis.py", "redis"),
        ("redis-python", "redis"),
    ],
)
def test_derived_includes(text: str, member: str) -> None:
    assert member in derived(text)


@pytest.mark.parametrize("text", ["", "-", "js", "py", ".js", "-py", "python-", "node-"])
def test_derived_never_contains_empty(text: str) -> None:
    assert "" not in derived(text)


def test_norm_collapses_separators_and_case() -> None:
    assert norm("A-b_C.d") == "abcd"


def test_derived_strips_only_one_affix() -> None:
    assert derived("node-thing-js") == {"nodethingjs", "thingjs", "nodething"}


REJECTED = [
    ("facebook", "react", pkg("@pika/react", 4035), set()),
    ("expressjs", "express", pkg("alemmi", 31001), set()),
    ("expressjs", "express", pkg("@depup/express", 100), set()),
    ("o", "r", pkg("r", 10**9, status="removed"), {("npm", "r")}),
    ("o", "r", pkg("r", None), set()),
    ("o", "r", pkg("r", True), set()),
    ("o", "r", pkg("r", "5"), set()),
    ("o", "r", pkg(123, 5), set()),
    ("o", "r", "not-a-dict", set()),
    ("o", "-", pkg("_", 5), set()),
    ("o", "r", pkg("r", 5, ecosystem=None), set()),
    ("o", "r", pkg("r", 5, period=None), set()),
    ("o", "r", pkg("r", 5, period=123), set()),
    ("o", "r", pkg("@o", 5), set()),
]
ACCEPTED = [
    ("babel", "babel", pkg("@babel/core", 1), set(), False),
    ("vuejs", "core", pkg("@vue/shared", 1), set(), False),
    ("mongodb", "mongo-python-driver", pkg("pymongo", 1, "pypi"), {("pypi", "pymongo")}, True),
    ("Pallets", "Flask", pkg("flask", 1, "pypi"), set(), False),
    ("Facebook", "react", pkg("@FACEBOOK/x", 1), set(), False),
    ("aws", "aws-cli", pkg("awscli", 0, "pypi"), set(), False),
    (
        "zopefoundation",
        "zope.interface",
        pkg("zope.interface", 1, "pypi"),
        {("pypi", "zope-interface")},
        True,
    ),
]


@pytest.mark.parametrize(("owner", "repo", "candidate", "attested"), REJECTED)
def test_rejected(
    owner: str,
    repo: str,
    candidate: object,
    attested: set[tuple[str, str]],
) -> None:
    assert pick_package([candidate], owner=owner, repo=repo, attested=attested) is None


@pytest.mark.parametrize(("owner", "repo", "candidate", "attested", "verified"), ACCEPTED)
def test_accepted(
    owner: str,
    repo: str,
    candidate: dict[str, object],
    attested: set[tuple[str, str]],
    verified: bool,
) -> None:
    result = pick_package([candidate], owner=owner, repo=repo, attested=attested)
    assert result is not None
    assert result.name == candidate["name"]
    assert result.verified is verified


def test_highest_downloads_wins() -> None:
    result = pick_package(
        [pkg("pytorch", 14792429, "conda", "total"), pkg("torch", 58964343, "pypi")],
        owner="pytorch",
        repo="pytorch",
        attested=set(),
    )
    assert result is not None
    assert result.name == "torch"
    assert result.downloads == 58964343
    assert result.period == "last-month"


def test_verified_reflects_winner() -> None:
    result = pick_package(
        [pkg("next", 280433182), pkg("@next/bundle-analyzer", 1000)],
        owner="vercel",
        repo="next.js",
        attested={("npm", "@next/bundle-analyzer")},
    )
    assert result is not None
    assert result.name == "next"
    assert result.verified is False


def test_tie_keeps_first() -> None:
    result = pick_package(
        [pkg("lib", 10, "npm"), pkg("lib", 10, "pypi")],
        owner="o",
        repo="lib",
        attested=set(),
    )
    assert result is not None
    assert result.ecosystem == "npm"


@pytest.mark.parametrize("period", ["total", "last-week"])
def test_period_passed_verbatim(period: str) -> None:
    result = pick_package([pkg("r", 1, period=period)], owner="o", repo="r", attested=set())
    assert result is not None
    assert result.period == period


@pytest.mark.parametrize(
    ("system", "name", "expected"),
    [
        ("PyPI", "Zope_Interface", ("pypi", "zope-interface")),
        ("NPM", "React", ("npm", "React")),
        ("npm", "a.b", ("npm", "a.b")),
        ("NuGet", "Newtonsoft.Json", ("nuget", "newtonsoft.json")),
    ],
)
def test_package_key(system: str, name: str, expected: tuple[str, str]) -> None:
    assert package_key(system, name) == expected


def test_attestation_identity_is_case_sensitive() -> None:
    assert pick_package([pkg("Foo", 1)], owner="o", repo="r", attested={("npm", "foo")}) is None
    result = pick_package([pkg("Foo", 1)], owner="o", repo="r", attested={("npm", "Foo")})
    assert result is not None
    assert result.verified is True


def version(system: object, name: object, provenance: object, **extra: object) -> dict[str, object]:
    return {
        "versionKey": {"system": system, "name": name, "version": "1"},
        "relationProvenance": provenance,
        "attestations": [{"verified": True}],
        **extra,
    }


def test_parse_attested() -> None:
    payload = {
        "versions": [
            version("NPM", "React", "SLSA_ATTESTATION"),
            version("PyPI", "Zope.Interface", "PYPI_PUBLISH_ATTESTATION"),
            version("RUBYGEMS", "rails", "RUBYGEMS_PUBLISH_ATTESTATION"),
            version("NPM", "unverified", "UNVERIFIED_METADATA"),
            version("GO", "example.com/foo", "GO_ORIGIN"),
            {"versionKey": None},
        ]
    }
    assert parse_attested(payload) == {
        ("npm", "React"),
        ("pypi", "zope-interface"),
        ("rubygems", "rails"),
    }


@pytest.mark.parametrize(
    "attestations",
    [[{"verified": False}], [{}], None, [{"verified": 1}], [{"verified": "true"}], {}],
)
def test_parse_attested_requires_verified_attestation(attestations: object) -> None:
    entry = version("NPM", "React", "SLSA_ATTESTATION", attestations=attestations)
    if attestations is None:
        entry.pop("attestations")
    assert parse_attested({"versions": [entry]}) == set()


def test_parse_attested_skips_malformed_entries() -> None:
    assert parse_attested(
        {
            "versions": [
                None,
                "entry",
                {"versionKey": None},
                version("NPM", "React", "SLSA_ATTESTATION", versionKey=None),
                version("NPM", "React", "SLSA_ATTESTATION", versionKey=[]),
                version(None, "React", "SLSA_ATTESTATION"),
                version("NPM", None, "SLSA_ATTESTATION"),
                version("NPM", "React", []),
                version(
                    "NPM", "React", "SLSA_ATTESTATION", attestations=[None, {}, {"verified": True}]
                ),
            ]
        }
    ) == {("npm", "React")}


@pytest.mark.parametrize("payload", [[], "x", {"versions": {}}])
def test_parse_attested_rejects_malformed(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_attested(payload)


def test_parse_attested_missing_versions_is_empty() -> None:
    assert parse_attested({}) == set()
