"""Migration 0033 (audit chain anchor) tests (Postgres-gated)."""

from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]

_TABLE = "audit_anchor"


def _alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    if output is not None:
        config.output_buffer = output
    return config


def test_offline_upgrade_emits_audit_anchor_sql() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output), "head", sql=True)
    assert "CREATE TABLE audit_anchor" in output.getvalue()


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())

    command.downgrade(config, "0032")
    assert _TABLE not in set(inspect(pg_engine).get_table_names())

    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())


def test_0033_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "0032")
    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())
