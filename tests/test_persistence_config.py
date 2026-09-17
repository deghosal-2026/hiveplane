"""Tests for persistence configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path

from hiveplane.config import Settings

_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_store_is_selectable() -> None:
    settings = Settings(execution={"store": "postgres"})
    assert settings.execution.store == "postgres"


def test_alembic_is_a_core_dependency() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any("alembic" in dep for dep in data["project"]["dependencies"])


def test_coverage_omits_live_db_glue() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    omit = data["tool"]["coverage"]["run"]["omit"]
    assert "src/hiveplane/persistence/run_store.py" in omit
    assert "src/hiveplane/persistence/postgres_audit.py" in omit
    assert "src/hiveplane/persistence/migrations/*" in omit
