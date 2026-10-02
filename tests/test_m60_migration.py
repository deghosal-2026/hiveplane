"""Migration 0035 (replay records) tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_TABLES = {"replays"}


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0035_adds_and_drops_replays(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES

    command.downgrade(config, "0034")
    assert not (_TABLES & set(inspect(pg_engine).get_table_names()))

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES
