"""Tests for optional adapter-dependency declaration."""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_langgraph_is_an_optional_extra() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any("langgraph" in dep for dep in extras["langgraph"])
    core = data["project"]["dependencies"]
    assert not any("langgraph" in dep for dep in core)


def test_ci_installs_the_langgraph_extra() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert ".[dev,langgraph]" in workflow
