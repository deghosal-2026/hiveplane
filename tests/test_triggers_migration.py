"""Trigger migration 0006 upgrade/downgrade tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0006_adds_and_drops_trigger_nonces(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    assert "trigger_nonces" in set(inspect(pg_engine).get_table_names())

    command.downgrade(config, "0005")
    assert "trigger_nonces" not in set(inspect(pg_engine).get_table_names())

    command.upgrade(config, "head")
    assert "trigger_nonces" in set(inspect(pg_engine).get_table_names())


def test_0006_is_idempotent(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert "trigger_nonces" in set(inspect(pg_engine).get_table_names())
