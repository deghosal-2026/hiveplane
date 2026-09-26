"""Migration 0011 (drift & quarantine) upgrade/downgrade tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_TABLES = {"quarantines", "drift_assessments"}


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0011_adds_and_drops_drift_tables(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    tables = set(inspect(pg_engine).get_table_names())
    assert tables >= _TABLES | {"drift_schedules"}

    command.downgrade(config, "0010")
    tables = set(inspect(pg_engine).get_table_names())
    assert not (_TABLES & tables)
    # drift_schedules is owned by 0001, so it survives the 0011 downgrade.
    assert "drift_schedules" in tables

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES


def test_0011_is_idempotent(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES
