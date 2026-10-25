from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
EXPECTED_CALLERS = {
    "dependency-safety.yml": "dependency-safety.yml",
    "dependency-safety-non-bot-gate.yml": "dependency-safety-non-bot-gate.yml",
    "pre-commit-autoupdate.yml": "pre-commit-autoupdate.yml",
    "security-scan.yml": "security.yml",
    "tag-release.yml": "tag-release.yml",
}
EXPECTED_ACTIONS = {"coverage": "ci.yml"}
SHARED_USES_RE = re.compile(
    r"^\s*uses:\s+j7an/shared-workflows/"
    r"(?:\.github/workflows/(?P<reusable>[^@\s]+)|actions/(?P<action>[^@\s]+))"
    r"@(?P<sha>[0-9a-f]{40})\s+#\s+(?P<version>v\d+\.\d+\.\d+)\s*$"
)
SHA_LITERAL_RE = re.compile(r"^[0-9A-Fa-f]{40}$")
VERSION_LITERAL_RE = re.compile(r"^v\d+\.\d+\.\d+$")
ACTION_PIN_LITERAL_RE = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9A-Fa-f]{40}"
    r'(?:\\?["\'])?\s+#\s+v\d+\.\d+\.\d+'
)


def _pin_literal_violations(path: Path, *, reject_standalone_values: bool) -> list[str]:
    violations: list[str] = []
    display_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if (
            ACTION_PIN_LITERAL_RE.search(value)
            or reject_standalone_values
            and (SHA_LITERAL_RE.fullmatch(value) or VERSION_LITERAL_RE.fullmatch(value))
        ):
            violations.append(f"{display_path}:{node.lineno}:{value}")

    return violations


def _scan(tests_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        policy_name = path.name.lower()
        violations.extend(
            _pin_literal_violations(
                path,
                reject_standalone_values="workflow" in policy_name or "action" in policy_name,
            )
        )
    return violations


def test_shared_workflow_contract_tests_do_not_snapshot_pin_values() -> None:
    violations = _scan(TESTS_DIR)
    assert not violations, "literal shared-workflow pin snapshots found:\n" + "\n".join(violations)


def test_pin_policy_reports_complete_snapshots_without_subject_marker(tmp_path: Path) -> None:
    sha = "a" * 40
    version = "v7" + ".1.2"
    target = "actions/checkout"
    snapshots = (
        f"uses: {target}@{sha} # {version}",
        f"uses: '{target}@{sha}' # {version}",
        f'uses: "{target}@{sha}" # {version}',
        f'uses: \\"{target}@{sha}\\" # {version}',
        f"before\nuses: {target}@{sha} # {version}\nafter",
    )
    path = tmp_path / "test_other.py"
    path.write_text(
        "\n".join(f"PIN_{index} = {snapshot!r}" for index, snapshot in enumerate(snapshots))
    )
    message = "\n".join(_scan(tmp_path))
    for snapshot in snapshots:
        pin_line = next(line for line in snapshot.splitlines() if target in line)
        assert pin_line in message


def test_pin_policy_reports_standalone_values_in_workflow_tests(tmp_path: Path) -> None:
    sha = "b" * 40
    version = "v8" + ".2.1"
    path = tmp_path / "test_workflow_contract.py"
    path.write_text(f"SHA = {sha!r}\nVERSION = {version!r}\n")
    message = "\n".join(_scan(tmp_path))
    assert sha in message
    assert version in message


def test_pin_policy_allows_incomplete_pins_and_unrelated_values(tmp_path: Path) -> None:
    sha = "c" * 40
    version = "v9" + ".3.0"
    target = "actions/checkout"
    path = tmp_path / "test_other.py"
    path.write_text(
        "\n".join(
            (
                f"SHA = {sha!r}",
                f"VERSION = {version!r}",
                f"MISSING_VERSION = {f'{target}@{sha}'!r}",
                f"MISSING_SHA = {f'{target}@v9 # {version}'!r}",
            )
        )
    )
    assert _scan(tmp_path) == []


def test_shared_workflows_refs_are_uniformly_pinned() -> None:
    shared_uses_lines: list[tuple[str, str]] = []

    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "j7an/shared-workflows/" in line:
                shared_uses_lines.append((path.name, line))

    assert len(shared_uses_lines) == len(EXPECTED_CALLERS) + len(EXPECTED_ACTIONS)

    actual_callers: dict[str, str] = {}
    actual_actions: dict[str, str] = {}
    actual_pins: set[tuple[str, str]] = set()
    for caller, line in shared_uses_lines:
        match = SHARED_USES_RE.fullmatch(line)
        assert match is not None, f"Malformed shared-workflows pin in {caller}: {line}"
        actual_pins.add((match["sha"], match["version"]))
        if match["reusable"]:
            actual_callers[match["reusable"]] = caller
        else:
            actual_actions[match["action"]] = caller

    assert actual_callers == EXPECTED_CALLERS
    assert actual_actions == EXPECTED_ACTIONS
    assert len(actual_pins) == 1, f"shared-workflows refs use different pins: {actual_pins}"


def test_release_workflow_retains_caller_owned_contract() -> None:
    """Pin the AGENTS.md "Release Policy" invariants, not implementation text."""
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")

    required_lines = (
        # Release chain: CI gate, build, TestPyPI publish, verify, PyPI publish, release.
        "needs: test",
        "needs: build",
        "needs: publish-testpypi",
        "needs: verify-testpypi",
        "needs: publish-pypi",
        # TestPyPI verification runs on the explicit Python version.
        'uv sync --python "$VERIFY_PYTHON"',
        'requires-python = ">=${VERIFY_PYTHON}"',
        # Ephemeral project whose explicit TestPyPI source applies only to the package.
        ".verify/pyproject.toml",
        "[tool.uv.sources]",
        '"${PACKAGE_NAME}" = { index = "testpypi" }',
        "[[tool.uv.index]]",
        'url = "https://test.pypi.org/simple/"',
        "explicit = true",
        # Hard gates: the exact built version, and tag ancestry on main.
        '"${PACKAGE_NAME}==${VERSION}",',
        'git merge-base --is-ancestor "$TAG_SHA" origin/main',
        # Caller-owned trusted publishing; a re-publish fails instead of skipping.
        "name: testpypi",
        "\n      name: pypi\n",
        "skip-existing: false",
    )

    for line in required_lines:
        assert line in release

    # An explicit X.Y version, checked by shape so a Python bump needs no test edit.
    assert re.search(r'^  VERIFY_PYTHON: "\d+\.\d+"$', release, re.MULTILINE)
    assert "--index-url" not in release
    assert "--extra-index-url" not in release


def test_testpypi_verifier_disables_setup_uv_cache() -> None:
    workflow = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")

    job_start = workflow.index("  verify-testpypi:")
    next_job = workflow.index("\n  publish-pypi:", job_start)
    verify_job = workflow[job_start:next_job]

    setup_start = verify_job.index("      - name: Set up uv")
    next_step = verify_job.index("\n      - name:", setup_start + 1)
    setup_step = verify_job[setup_start:next_step]

    assert "uses: astral-sh/setup-uv@" in setup_step
    assert "with:" in setup_step
    assert "enable-cache: false" in setup_step
    assert "ignore-empty-workdir: true" in setup_step


def _job_text(workflow: str, job: str) -> str:
    start = workflow.index(f"\n  {job}:") + 1
    match = re.search(r"^  [A-Za-z0-9_-]+:\s*$", workflow[start + 1 :], re.MULTILINE)
    return workflow[start : start + 1 + match.start()] if match else workflow[start:]


def _server_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    return data


def test_readme_declares_registry_name() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {_server_json()['name']} -->" in readme


def test_server_json_identifier_matches_release_package() -> None:
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")
    match = re.search(r"^  PACKAGE_NAME: (\S+)$", release, re.MULTILINE)
    assert match is not None
    server = _server_json()
    assert server["packages"][0]["identifier"] == match.group(1)
    env_var = server["packages"][0]["environmentVariables"][0]
    assert env_var["name"] == "DEP_RANK_TOKEN"
    assert env_var["isRequired"] is True
    assert env_var["isSecret"] is True


def test_registry_job_contract() -> None:
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")
    job = _job_text(release, "publish-mcp-registry")
    for needle in (
        "needs: github-release",
        "needs.github-release.outputs.prerelease == 'false'",
        ".version = $v",
        ".packages[0].version = $v",
        "releases/download/",
        "sha256sum --check --strict",
        "mcp-publisher login github-oidc",
        "mcp-publisher publish",
        "persist-credentials: false",
        "id-token: write",
        "contents: read",
    ):
        assert needle in job
    assert "releases/latest" not in job


def test_github_release_exposes_prerelease_output() -> None:
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")
    job = _job_text(release, "github-release")
    assert "prerelease: ${{ steps." in job
    assert '>> "$GITHUB_OUTPUT"' in job


EXPORT_RE = re.compile(r"^\s*(?:- run: |run: )?(uv export [^\n]+)$", re.MULTILINE)


def test_dockerfile_python_matches_verify_python_and_is_digest_pinned() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")
    base = re.search(r"^FROM python:(\d+\.\d+)-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    verify = re.search(r'^  VERIFY_PYTHON: "(\d+\.\d+)"$', release, re.M)
    assert base is not None and verify is not None
    assert base.group(1) == verify.group(1)


def test_dockerignore_denies_everything_but_build_inputs() -> None:
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines[0] == "*"
    assert set(lines[1:]) == {"!dist/*.whl", "!dist/requirements.txt"}


def test_docker_build_job_contract() -> None:
    ci = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
    job = _job_text(ci, "docker-build")
    for needle in (
        "needs: [update-lockfile]",
        "contents: read",
        "persist-credentials: false",
        "fetch-depth: 0",
        "uv build --wheel --out-dir dist",
        "docker build",
        "scripts/smoke-test-image.sh",
    ):
        assert needle in job
    assert not re.search(r"^\s*push:|docker push|--push", job, re.MULTILINE)
    assert "packages:" not in job
    on_block = ci[: ci.index("\njobs:")]
    assert "paths" not in on_block


def _permissions_block(job: str) -> set[str]:
    block = job[job.index("    permissions:\n") :].split("\n    steps:")[0]
    return {line.strip() for line in block.splitlines()[1:] if line.strip()}


def test_publish_ghcr_job_contract() -> None:
    release = (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")
    ci = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
    job = _job_text(release, "publish-ghcr")
    assert "needs: publish-pypi" in job
    assert _permissions_block(job) == {
        "contents: read",
        "packages: write",
        "id-token: write",
        "attestations: write",
    }
    for needle in (
        "persist-credentials: false",
        "enable-cache: false",
        "name: pypi-dist",
        "platforms: linux/amd64,linux/arm64",
        "registry: ghcr.io",
        "secrets.GITHUB_TOKEN",
        "scripts/smoke-test-image.sh",
        "push-to-registry: true",
        "scripts/classify-prerelease.sh",
        "imagetools create",
    ):
        assert needle in job
    # latest is added only after the smoke test, and only for stable releases.
    assert job.index("scripts/smoke-test-image.sh") < job.index("imagetools create")
    latest_step = job[job.rindex("\n      - name:", 0, job.index("imagetools create")) :]
    assert "== 'false'" in latest_step.split("\n      - name:")[1]
    assert ":latest" not in job[: job.index("imagetools create")]
    # One export command shared with the PR-time build.
    assert EXPORT_RE.findall(job) == EXPORT_RE.findall(_job_text(ci, "docker-build"))
    assert len(EXPORT_RE.findall(job)) == 1


def test_dependabot_docker_entry_holds_python_minor() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    start = config.index('package-ecosystem: "docker"')
    nxt = config.find("package-ecosystem:", start + 1)
    entry = config[start : nxt if nxt != -1 else len(config)]
    assert "version-update:semver-minor" in entry
    assert "version-update:semver-major" in entry
    assert "cooldown:" in entry
