from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

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
SHARED_USES_RE = re.compile(
    r"^\s*uses:\s+j7an/shared-workflows/\.github/workflows/"
    r"(?P<reusable>[^@\s]+)@(?P<sha>[0-9a-f]{40})\s+"
    r"#\s+(?P<version>v\d+\.\d+\.\d+)\s*$"
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
    tree = ast.parse(path.read_text())

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


def test_shared_workflow_contract_tests_do_not_snapshot_pin_values() -> None:
    violations: list[str] = []

    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        policy_name = path.name.lower()
        violations.extend(
            _pin_literal_violations(
                path,
                reject_standalone_values="workflow" in policy_name or "action" in policy_name,
            )
        )

    assert not violations, "literal shared-workflow pin snapshots found:\n" + "\n".join(violations)


def test_pin_policy_reports_complete_snapshots_without_subject_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setitem(globals(), "TESTS_DIR", tmp_path)

    with pytest.raises(AssertionError) as exc_info:
        test_shared_workflow_contract_tests_do_not_snapshot_pin_values()

    message = str(exc_info.value)
    for snapshot in snapshots:
        pin_line = next(line for line in snapshot.splitlines() if target in line)
        assert pin_line in message


def test_pin_policy_reports_standalone_values_in_workflow_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "b" * 40
    version = "v8" + ".2.1"
    path = tmp_path / "test_workflow_contract.py"
    path.write_text(f"SHA = {sha!r}\nVERSION = {version!r}\n")
    monkeypatch.setitem(globals(), "TESTS_DIR", tmp_path)

    with pytest.raises(AssertionError) as exc_info:
        test_shared_workflow_contract_tests_do_not_snapshot_pin_values()

    assert sha in str(exc_info.value)
    assert version in str(exc_info.value)


def test_pin_policy_allows_incomplete_pins_and_unrelated_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setitem(globals(), "TESTS_DIR", tmp_path)

    test_shared_workflow_contract_tests_do_not_snapshot_pin_values()


def test_shared_workflows_refs_are_uniformly_pinned() -> None:
    shared_uses_lines: list[tuple[str, str]] = []

    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        for line in path.read_text().splitlines():
            if "j7an/shared-workflows/.github/workflows/" in line:
                shared_uses_lines.append((path.name, line))

    assert len(shared_uses_lines) == len(EXPECTED_CALLERS)

    actual_callers: dict[str, str] = {}
    actual_pins: set[tuple[str, str]] = set()
    for caller, line in shared_uses_lines:
        match = SHARED_USES_RE.fullmatch(line)
        assert match is not None, f"Malformed shared-workflows pin in {caller}: {line}"
        actual_pins.add((match["sha"], match["version"]))
        actual_callers[match["reusable"]] = caller

    assert actual_callers == EXPECTED_CALLERS
    assert len(actual_pins) == 1, f"shared-workflows callers use different pins: {actual_pins}"


def test_release_workflow_retains_caller_owned_contract() -> None:
    release = (WORKFLOWS_DIR / "release.yml").read_text()

    required_lines = (
        'VERIFY_PYTHON: "3.13"',
        "needs: test",
        "needs: build",
        "needs: publish-testpypi",
        "needs: verify-testpypi",
        "needs: publish-pypi",
        r"grep -qE '^[0-9]+(\.[0-9]+){1,2}$'",
        r"grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
        "for SLEEP_SECONDS in 30 60 90 120 150; do",
        "rm -rf .verify",
        "mkdir -p .verify",
        ".verify/pyproject.toml",
        'requires-python = ">=${VERIFY_PYTHON}"',
        '"${PACKAGE_NAME}==${VERSION}",',
        "[tool.uv.sources]",
        '"${PACKAGE_NAME}" = { index = "testpypi" }',
        "[[tool.uv.index]]",
        'url = "https://test.pypi.org/simple/"',
        "explicit = true",
        'uv sync --python "$VERIFY_PYTHON" --refresh-package "$PACKAGE_NAME"',
        'uv run --no-sync bash -euo pipefail -c "$VERIFY_COMMAND"',
        'git merge-base --is-ancestor "$TAG_SHA" origin/main',
        "name: testpypi",
        "\n      name: pypi\n",
        "skip-existing: false",
        "if: env.ATTACH_ASSETS == 'true'",
        'if [ "$DRAFT_RELEASE" = "true" ]; then',
    )

    for line in required_lines:
        assert line in release

    assert "--index-url" not in release
    assert "--extra-index-url" not in release


def test_testpypi_verifier_disables_setup_uv_cache() -> None:
    workflow = (WORKFLOWS_DIR / "release.yml").read_text()

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
