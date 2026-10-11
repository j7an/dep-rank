from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
DEPENDENCY_SAFETY = ROOT / ".github" / "workflows" / "dependency-safety.yml"
PYPROJECT = ROOT / "pyproject.toml"
UV_SPAN_RE = re.compile(r"(?P<count>\d+) (?P<unit>day|week)s?")
UV_UNIT_DAYS = {"day": 1, "week": 7}


def _uv_exclude_newer_days() -> int:
    value = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]["exclude-newer"]
    match = UV_SPAN_RE.fullmatch(value)
    assert match, f"tool.uv.exclude-newer must be 'N days' or 'N weeks', got {value!r}"
    return int(match["count"]) * UV_UNIT_DAYS[match["unit"]]


def test_release_age_settings_agree() -> None:
    """The release-age window is one policy spread over three files.

    This checks agreement only; the security workflow's Zizmor gate enforces the minimum.
    """
    dependabot = DEPENDABOT.read_text(encoding="utf-8")
    cooldowns = re.findall(r"^\s+default-days: (\d+)\s*$", dependabot, re.M)
    updaters = re.findall(r"^\s+- package-ecosystem:", dependabot, re.M)
    assert updaters, "dependabot.yml has no updaters"
    assert len(cooldowns) == len(updaters), "every Dependabot updater needs cooldown.default-days"

    safety = DEPENDENCY_SAFETY.read_text(encoding="utf-8")
    gate = re.findall(r"^\s+minimum_release_age_days: (\d+)\s*$", safety, re.M)
    assert len(gate) == 1, "dependency-safety.yml needs exactly one minimum_release_age_days"

    settings = {
        **{f"dependabot.yml cooldown #{i}": int(d) for i, d in enumerate(cooldowns, 1)},
        "dependency-safety.yml minimum_release_age_days": int(gate[0]),
        "pyproject.toml tool.uv.exclude-newer": _uv_exclude_newer_days(),
    }
    assert len(set(settings.values())) == 1, f"release-age settings disagree: {settings}"
