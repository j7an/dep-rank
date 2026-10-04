"""Isolated cache storage for in-process CLI tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _cli_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("dep_rank.cli.app._cache_dir", lambda: str(tmp_path / "cache"))
