from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
EXPECTED_SHA = "e22b38d70bf615d2e250718d430a5a4688fee158"
EXPECTED_VERSION = "v4.2.3"
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


def test_shared_workflows_refs_are_uniformly_pinned() -> None:
    shared_uses_lines: list[tuple[str, str]] = []

    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        for line in path.read_text().splitlines():
            if "j7an/shared-workflows/.github/workflows/" in line:
                shared_uses_lines.append((path.name, line))

    assert len(shared_uses_lines) == len(EXPECTED_CALLERS)

    actual_callers: dict[str, str] = {}
    for caller, line in shared_uses_lines:
        match = SHARED_USES_RE.fullmatch(line)
        assert match is not None, f"Malformed shared-workflows pin in {caller}: {line}"
        assert match["sha"] == EXPECTED_SHA
        assert match["version"] == EXPECTED_VERSION
        actual_callers[match["reusable"]] = caller

    assert actual_callers == EXPECTED_CALLERS


def test_release_workflow_retains_v4_2_3_contract() -> None:
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
