"""Migrations 0019 (shadow) and 0020 (canary/experiments) tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_SHADOW = {"shadow_runs"}
_CANARY = {
    "canary_rollouts",
    "canary_samples",
    "experiment_campaigns",
    "experiment_arms",
}


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0020_adds_and_drops_canary_and_experiment_tables(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _SHADOW | _CANARY

    command.downgrade(config, "0019")
    tables = set(inspect(pg_engine).get_table_names())
    assert not (_CANARY & tables)
    assert tables >= _SHADOW

    command.downgrade(config, "0018")
    assert not (_SHADOW & set(inspect(pg_engine).get_table_names()))

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _SHADOW | _CANARY


def test_m37_m38_migrations_are_idempotent(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _SHADOW | _CANARY
