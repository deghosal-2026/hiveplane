"""Migration 0034 (tenant lifecycle) tests (Postgres-gated) (M58-06)."""

from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]

_TABLE = "tenants"
_COLUMN = "status"


def _alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    if output is not None:
        config.output_buffer = output
    return config


def _column_names(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(_TABLE)}


def test_offline_upgrade_emits_tenant_status_sql() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output), "head", sql=True)
    emitted = output.getvalue()
    assert "ADD COLUMN status" in emitted


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert _COLUMN in _column_names(pg_engine)

    command.downgrade(config, "0033")
    assert _COLUMN not in _column_names(pg_engine)

    command.upgrade(config, "head")
    assert _COLUMN in _column_names(pg_engine)


def test_0034_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "0033")
    command.upgrade(config, "head")
    assert _COLUMN in _column_names(pg_engine)
