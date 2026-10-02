"""Migration 0031 upgrade/downgrade and table-presence tests (Postgres-gated)."""

from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]

_REPORTING_TABLES = {
    "report_runs",
    "report_schedules",
    "audit_exports",
    "evidence_packs",
    "purge_records",
}


def _alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    if output is not None:
        config.output_buffer = output
    return config


def test_offline_upgrade_emits_reporting_table_sql() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output), "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE report_runs" in sql
    assert "CREATE TABLE purge_records" in sql


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _REPORTING_TABLES

    command.downgrade(config, "0030")
    assert not (_REPORTING_TABLES & set(inspect(pg_engine).get_table_names()))

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _REPORTING_TABLES


def test_0031_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "0030")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _REPORTING_TABLES
