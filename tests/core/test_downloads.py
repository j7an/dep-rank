"""Behavioral tests for display-only package matching."""

from __future__ import annotations

from urllib.parse import quote

import httpx2
import pytest

from dep_rank.core import downloads as downloads_module
from dep_rank.core.downloads import derived, norm, package_key, parse_attested, pick_package
from dep_rank.core.models import DownloadsCheckResult, PackageDownloads
from tests.conftest import FakeHTTP, make_repo


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
    ("o", "r", pkg("@r", 5), set()),  # malformed scope never falls through to the repo name
    ("o", "r", pkg("x/r", 5, "packagist"), set()),  # namespace only, as with @pika/react
    # Namespace naming the repo rather than the owner stays rejected.
    ("foambubble", "foam", pkg("foam/foam-vscode", 354366, "openvsx", "total"), set()),
]
ACCEPTED = [
    ("babel", "babel", pkg("@babel/core", 1), set(), False),
    ("vuejs", "core", pkg("@vue/shared", 1), set(), False),
    ("mongodb", "mongo-python-driver", pkg("pymongo", 1, "pypi"), {("pypi", "pymongo")}, True),
    ("Pallets", "Flask", pkg("flask", 1, "pypi"), set(), False),
    ("Facebook", "react", pkg("@FACEBOOK/x", 1), set(), False),
    ("aws", "aws-cli", pkg("awscli", 0, "pypi"), set(), False),
    (
        "docker-php",
        "docker-php",
        pkg("docker-php/docker-php", 631920, "packagist", "total"),
        set(),
        False,
    ),
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


LOOKUP = "https://packages.ecosyste.ms/api/v1/packages/lookup"


def eco_url(owner: str, name: str) -> str:
    return str(
        httpx2.URL(
            LOOKUP,
            params={
                "repository_url": f"https://github.com/{owner}/{name}",
                "sort": "downloads",
                "order": "desc",
                "per_page": 50,
            },
        )
    )


def name_url(ecosystem: str, name: str) -> str:
    return str(httpx2.URL(LOOKUP, params={"ecosystem": ecosystem, "name": name}))


def npm_url(name: str) -> str:
    return "https://api.npmjs.org/downloads/point/last-month/" + quote(name, safe="@")


def dd_url(owner: str, name: str) -> str:
    return f"https://api.deps.dev/v3/projects/github.com%2F{owner}%2F{name}:packageversions"


def attest(system: str, name: str, kind: str = "SLSA_ATTESTATION") -> dict[str, object]:
    return {
        "versionKey": {"system": system, "name": name, "version": "1"},
        "relationProvenance": kind,
        "attestations": [{"verified": True}],
    }


def total_requests(mock_http: FakeHTTP) -> int:
    return sum(len(calls) for calls in mock_http.requests.values())


def matched_row(mock_http: FakeHTTP, name: str) -> None:
    mock_http.get(eco_url("o", name), payload=[pkg(name, 10)])
    mock_http.get(dd_url("o", name), payload={"versions": []})
    mock_http.get(npm_url(name), payload={"downloads": 10, "package": name})


class TestFetchDownloads:
    async def test_name_match_found_unverified(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(
            eco_url("expressjs", "express"),
            payload=[pkg("express", 636140021), pkg("alemmi", 31001)],
        )
        mock_http.get(dd_url("expressjs", "express"), payload={"versions": []})
        mock_http.get(npm_url("express"), payload={"downloads": 636140021, "package": "express"})
        original = make_repo("expressjs", "express")
        rows, result = await downloads_module.fetch_downloads(session, [original])
        assert rows[0].downloads == PackageDownloads(
            ecosystem="npm",
            name="express",
            downloads=636140021,
            period="last-month",
            verified=False,
        )
        assert rows[0] is not original
        assert original.downloads is None
        assert result.complete is True
        assert total_requests(mock_http) == 3

    async def test_attested_missing_candidate_is_fetched_by_name(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(
            eco_url("facebook", "react"),
            payload=[pkg("babel-plugin-react-compiler", 56625705), pkg("@pika/react", 4035)],
        )
        mock_http.get(dd_url("facebook", "react"), payload={"versions": [attest("NPM", "react")]})
        mock_http.get(name_url("npm", "react"), payload=[pkg("react", 636140021)])
        mock_http.get(npm_url("react"), payload={"downloads": 828607491, "package": "react"})
        rows, result = await downloads_module.fetch_downloads(
            session, [make_repo("facebook", "react")]
        )
        assert rows[0].downloads is not None
        assert rows[0].downloads.downloads == 828607491
        assert rows[0].downloads.name == "react"
        assert rows[0].downloads.verified is True
        assert result.complete is True
        assert len(mock_http.requests[("GET", name_url("npm", "react"))]) == 1

    @pytest.mark.parametrize(
        ("owner", "repo", "name", "proof"),
        [
            ("mongodb", "mongo-python-driver", "pymongo", "pymongo"),
            ("zopefoundation", "zope.interface", "zope.interface", "zope-interface"),
        ],
    )
    async def test_attested_present_candidate_skips_gap_fill(
        self,
        mock_http: FakeHTTP,
        session: httpx2.AsyncClient,
        owner: str,
        repo: str,
        name: str,
        proof: str,
    ) -> None:
        mock_http.get(eco_url(owner, repo), payload=[pkg(name, 5, "pypi")])
        mock_http.get(dd_url(owner, repo), payload={"versions": [attest("PYPI", proof)]})
        rows, _ = await downloads_module.fetch_downloads(session, [make_repo(owner, repo)])
        assert rows[0].downloads is not None
        assert rows[0].downloads.name == name
        assert rows[0].downloads.verified is True
        assert total_requests(mock_http) == 2

    async def test_gap_fill_uses_exact_case(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "r"), payload=[])
        mock_http.get(dd_url("o", "r"), payload={"versions": [attest("NPM", "JSONStream")]})
        mock_http.get(name_url("npm", "JSONStream"), payload=[pkg("JSONStream", 5)])
        mock_http.get(npm_url("JSONStream"), payload={"downloads": 5, "package": "JSONStream"})
        rows, _ = await downloads_module.fetch_downloads(session, [make_repo("o", "r")])
        assert rows[0].downloads is not None
        assert rows[0].downloads.name == "JSONStream"
        assert rows[0].downloads.verified is True
        assert len(mock_http.requests[("GET", name_url("npm", "JSONStream"))]) == 1

    async def test_gap_fill_empty_is_none_not_unavailable(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("facebook", "react"), payload=[])
        mock_http.get(dd_url("facebook", "react"), payload={"versions": [attest("NPM", "react")]})
        mock_http.get(name_url("npm", "react"), payload=[])
        rows, result = await downloads_module.fetch_downloads(
            session, [make_repo("facebook", "react")]
        )
        assert rows[0].downloads is None
        assert result == DownloadsCheckResult(complete=True, unavailable=[])

    async def test_deps_dev_404_means_no_attestations(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 10)])
        mock_http.get(dd_url("o", "a"), status=404)
        mock_http.get(npm_url("a"), payload={"downloads": 10, "package": "a"})
        rows, result = await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert rows[0].downloads is not None
        assert rows[0].downloads.verified is False
        assert result.complete is True

    async def test_empty_candidates_is_none(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[])
        mock_http.get(dd_url("o", "a"), payload={"versions": []})
        rows, result = await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert rows[0].downloads is None
        assert result.complete is True

    @pytest.mark.parametrize(
        "failure", ["eco-object", "dd-list", "eco-json", "dd-versions", "dd-json"]
    )
    async def test_malformed_body_marks_only_that_row(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient, failure: str
    ) -> None:
        if failure.startswith("eco"):
            if failure == "eco-object":
                mock_http.get(eco_url("o", "a"), payload={"error": "x"})
            else:
                mock_http.get(eco_url("o", "a"), body="{", content_type="application/json")
        else:
            mock_http.get(eco_url("o", "a"), payload=[pkg("a", 10)])
            if failure == "dd-json":
                mock_http.get(dd_url("o", "a"), body="{", content_type="application/json")
            else:
                mock_http.get(
                    dd_url("o", "a"), payload=[] if failure == "dd-list" else {"versions": {}}
                )
        matched_row(mock_http, "b")
        rows, result = await downloads_module.fetch_downloads(
            session, [make_repo("o", "a"), make_repo("o", "b")]
        )
        assert result.unavailable == ["o/a"]
        assert result.complete is False
        assert rows[0].downloads is None
        assert rows[1].downloads is not None

    @pytest.mark.parametrize(("service", "status"), [("eco", 400), ("eco", 404), ("dd", 403)])
    async def test_other_4xx_marks_row_and_continues(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient, service: str, status: int
    ) -> None:
        if service == "dd":
            mock_http.get(eco_url("o", "a"), payload=[pkg("a", 10)])
        mock_http.get(eco_url("o", "a") if service == "eco" else dd_url("o", "a"), status=status)
        matched_row(mock_http, "b")
        rows, result = await downloads_module.fetch_downloads(
            session, [make_repo("o", "a"), make_repo("o", "b")]
        )
        assert result.unavailable == ["o/a"]
        assert rows[0].downloads is None
        assert rows[1].downloads is not None

    @pytest.mark.parametrize(
        ("service", "failure"),
        [
            ("eco", httpx2.ReadTimeout("slow")),
            ("eco", httpx2.ConnectError("down")),
            ("eco", 429),
            ("eco", 503),
            ("dd", 503),
        ],
    )
    async def test_service_failure_stops_pass(
        self,
        mock_http: FakeHTTP,
        session: httpx2.AsyncClient,
        service: str,
        failure: int | httpx2.RequestError,
    ) -> None:
        matched_row(mock_http, "a")
        if service == "dd":
            mock_http.get(eco_url("o", "b"), payload=[pkg("b", 10)])
        url = eco_url("o", "b") if service == "eco" else dd_url("o", "b")
        if isinstance(failure, int):
            mock_http.get(url, status=failure)
        else:
            mock_http.get(url, exception=failure)
        originals = [make_repo("o", name) for name in "abcd"]
        rows, result = await downloads_module.fetch_downloads(session, originals)
        assert result == DownloadsCheckResult(complete=False, unavailable=["o/b", "o/c", "o/d"])
        assert rows[0].downloads is not None
        assert [r.name for r in rows] == list("abcd")
        assert all(row is not original for row, original in zip(rows, originals, strict=True))
        assert all(row.downloads is None for row in rows[1:])
        assert ("GET", eco_url("o", "c")) not in mock_http.requests
        assert ("GET", eco_url("o", "d")) not in mock_http.requests
        assert total_requests(mock_http) == (5 if service == "dd" else 4)

    async def test_requests_carry_user_agent(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[])
        mock_http.get(dd_url("o", "a"), payload={"versions": [attest("NPM", "x")]})
        mock_http.get(name_url("npm", "x"), payload=[])
        await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert total_requests(mock_http) == 3
        assert all(
            request.headers["User-Agent"].startswith("dep-rank/")
            for calls in mock_http.requests.values()
            for request in calls
        )

    async def test_caller_credentials_are_not_sent(self, mock_http: FakeHTTP) -> None:
        mock_http.get(eco_url("expressjs", "express"), payload=[pkg("express", 5)])
        mock_http.get(npm_url("express"), payload={"downloads": 5, "package": "express"})
        mock_http.get(dd_url("expressjs", "express"), payload={"versions": [attest("NPM", "x")]})
        mock_http.get(name_url("npm", "x"), payload=[])
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer ghp_x"},
            auth=("u", "p"),
            cookies={"user_session": "x"},
            follow_redirects=True,
        ) as authed:
            rows, _ = await downloads_module.fetch_downloads(
                authed, [make_repo("expressjs", "express")]
            )
            assert rows[0].downloads is not None
            location = "https://packages.ecosyste.ms/elsewhere"
            mock_http.get(eco_url("o", "a"), status=302, headers={"Location": location})
            _, result = await downloads_module.fetch_downloads(authed, [make_repo("o", "a")])
        assert result.unavailable == ["o/a"]
        assert ("GET", location) not in mock_http.requests
        assert total_requests(mock_http) == 5
        assert any(
            request.url.host == "api.npmjs.org"
            for calls in mock_http.requests.values()
            for request in calls
        )
        assert all(
            "Authorization" not in request.headers and "Cookie" not in request.headers
            for calls in mock_http.requests.values()
            for request in calls
        )

    async def test_empty_repos_make_no_requests(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        assert await downloads_module.fetch_downloads(session, []) == (
            [],
            DownloadsCheckResult(complete=True, unavailable=[]),
        )
        assert total_requests(mock_http) == 0

    @pytest.mark.parametrize(
        "failure", [400, 429, 503, "json", "object", httpx2.ReadTimeout("slow")]
    )
    async def test_gap_fill_failure_discards_partial_row(
        self,
        mock_http: FakeHTTP,
        session: httpx2.AsyncClient,
        failure: int | str | httpx2.RequestError,
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 100)])
        mock_http.get(dd_url("o", "a"), payload={"versions": [attest("NPM", "x")]})
        url = name_url("npm", "x")
        if isinstance(failure, int):
            mock_http.get(url, status=failure)
        elif isinstance(failure, httpx2.RequestError):
            mock_http.get(url, exception=failure)
        elif failure == "json":
            mock_http.get(url, body="{", content_type="application/json")
        else:
            mock_http.get(url, payload={})
        matched_row(mock_http, "b")
        rows, result = await downloads_module.fetch_downloads(
            session, [make_repo("o", "a"), make_repo("o", "b")]
        )
        assert rows[0].downloads is None
        stops = isinstance(failure, httpx2.RequestError) or failure in (429, 503)
        assert result.unavailable == (["o/a", "o/b"] if stops else ["o/a"])
        assert (rows[1].downloads is None) is stops
        assert total_requests(mock_http) == (3 if stops else 6)

    async def test_gap_fill_skips_unsupported_and_orders_missing_keys(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        seen: list[str] = []

        async def response(request: httpx2.Request) -> httpx2.Response:
            seen.append(str(request.url))
            return httpx2.Response(200, json=[pkg(request.url.params["name"], 5)])

        mock_http.get(eco_url("o", "r"), payload=[None, pkg(None, 1)])
        mock_http.get(
            dd_url("o", "r"),
            payload={
                "versions": [
                    attest("UNSUPPORTED", "x"),
                    attest("UNKNOWN", "x"),
                    attest("NPM", "z"),
                    attest("NPM", "a"),
                    attest("NPM", "a"),
                ]
            },
        )
        for name in ("a", "z"):
            mock_http.get(name_url("npm", name), callback=response)
        mock_http.get(npm_url("a"), payload={"downloads": 5, "package": "a"})
        rows, result = await downloads_module.fetch_downloads(session, [make_repo("o", "r")])
        assert seen == [name_url("npm", "a"), name_url("npm", "z")]
        assert result.complete is True
        assert rows[0].downloads is not None
        assert rows[0].downloads.name == "a"
        assert total_requests(mock_http) == 5

    async def test_go_attestation_gap_fill_preserves_package_name(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "r"), payload=[])
        mock_http.get(
            dd_url("o", "r"), payload={"versions": [attest("GO", "example.com/Owner/Lib")]}
        )
        mock_http.get(
            name_url("go", "example.com/Owner/Lib"), payload=[pkg("example.com/Owner/Lib", 7, "go")]
        )
        rows, result = await downloads_module.fetch_downloads(session, [make_repo("o", "r")])
        assert result.complete is True
        assert rows[0].downloads == PackageDownloads(
            ecosystem="go",
            name="example.com/Owner/Lib",
            downloads=7,
            period="last-month",
            verified=True,
        )
        assert len(mock_http.requests[("GET", name_url("go", "example.com/Owner/Lib"))]) == 1

    async def test_npm_winner_count_comes_from_npm(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("elizaOS", "eliza"), payload=[pkg("@elizaos/core", 197042)])
        mock_http.get(dd_url("elizaOS", "eliza"), payload={"versions": []})
        mock_http.get(
            npm_url("@elizaos/core"), payload={"downloads": 42274, "package": "@elizaos/core"}
        )
        repos, check = await downloads_module.fetch_downloads(
            session, [make_repo("elizaOS", "eliza")]
        )
        assert repos[0].downloads == PackageDownloads(
            ecosystem="npm",
            name="@elizaos/core",
            downloads=42274,
            period="last-month",
            verified=False,
        )
        assert check.complete is True
        # FakeHTTP keys on the exact URL, proving the @elizaos%2Fcore encoding.
        assert len(mock_http.requests[("GET", npm_url("@elizaos/core"))]) == 1

    async def test_non_npm_winner_skips_npm(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(
            eco_url("mongodb", "mongo-python-driver"), payload=[pkg("pymongo", 64665876, "pypi")]
        )
        mock_http.get(
            dd_url("mongodb", "mongo-python-driver"),
            payload={"versions": [attest("PYPI", "pymongo")]},
        )
        rows, _ = await downloads_module.fetch_downloads(
            session, [make_repo("mongodb", "mongo-python-driver")]
        )
        assert rows[0].downloads is not None
        assert rows[0].downloads.downloads == 64665876
        assert not any(url.startswith("https://api.npmjs.org/") for _, url in mock_http.requests)

    async def test_npm_404_means_no_package(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 100), pkg("@o/b", 50)])
        mock_http.get(dd_url("o", "a"), payload={"versions": []})
        mock_http.get(npm_url("a"), status=404, payload={"error": "package a not found"})
        rows, check = await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert rows[0].downloads is None
        assert check.unavailable == []
        assert ("GET", npm_url("@o/b")) not in mock_http.requests

    @pytest.mark.parametrize(
        "payload", [{"downloads": "x"}, {"downloads": True}, {"downloads": 1.0}, {}, []]
    )
    async def test_npm_malformed_body_marks_row(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient, payload: object
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 100)])
        mock_http.get(dd_url("o", "a"), payload={"versions": []})
        mock_http.get(npm_url("a"), payload=payload)
        matched_row(mock_http, "b")
        rows, check = await downloads_module.fetch_downloads(
            session, [make_repo("o", "a"), make_repo("o", "b")]
        )
        assert check.unavailable == ["o/a"]
        assert rows[0].downloads is None
        assert rows[1].downloads is not None

    @pytest.mark.parametrize("failure", [429, 503, httpx2.ReadTimeout("slow")])
    async def test_npm_failure_stops_pass(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient, failure: int | httpx2.RequestError
    ) -> None:
        matched_row(mock_http, "a")
        mock_http.get(eco_url("o", "b"), payload=[pkg("b", 10)])
        mock_http.get(dd_url("o", "b"), payload={"versions": []})
        if isinstance(failure, int):
            mock_http.get(npm_url("b"), status=failure)
        else:
            mock_http.get(npm_url("b"), exception=failure)
        rows, check = await downloads_module.fetch_downloads(
            session, [make_repo("o", name) for name in "abc"]
        )
        assert check.unavailable == ["o/b", "o/c"]
        assert rows[0].downloads is not None
        assert all(row.downloads is None for row in rows[1:])
        assert ("GET", eco_url("o", "c")) not in mock_http.requests

    async def test_npm_zero_downloads_is_a_count(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 100)])
        mock_http.get(dd_url("o", "a"), payload={"versions": []})
        mock_http.get(npm_url("a"), payload={"downloads": 0})
        rows, check = await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert rows[0].downloads is not None
        assert rows[0].downloads.downloads == 0
        assert check.unavailable == []

    async def test_only_winner_requests_npm(
        self, mock_http: FakeHTTP, session: httpx2.AsyncClient
    ) -> None:
        mock_http.get(eco_url("o", "a"), payload=[pkg("a", 100), pkg("@o/b", 50)])
        mock_http.get(dd_url("o", "a"), payload={"versions": []})
        mock_http.get(npm_url("a"), payload={"downloads": 10, "package": "a"})
        rows, _ = await downloads_module.fetch_downloads(session, [make_repo("o", "a")])
        assert rows[0].downloads is not None
        assert rows[0].downloads.name == "a"
        npm_requests = [
            str(request.url)
            for calls in mock_http.requests.values()
            for request in calls
            if request.url.host == "api.npmjs.org"
        ]
        assert npm_requests == [npm_url("a")]
