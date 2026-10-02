"""Migration 0032 (notification preferences) tests (Postgres-gated)."""

from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]

_TABLE = "notification_preferences"


def _alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    if output is not None:
        config.output_buffer = output
    return config


def test_offline_upgrade_emits_notification_preferences_sql() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output), "head", sql=True)
    assert "CREATE TABLE notification_preferences" in output.getvalue()


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())

    command.downgrade(config, "0031")
    assert _TABLE not in set(inspect(pg_engine).get_table_names())

    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())


def test_0032_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "0031")
    command.upgrade(config, "head")
    assert _TABLE in set(inspect(pg_engine).get_table_names())
