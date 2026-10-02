"""Migration 0014 (run feedback) upgrade/downgrade tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]
_TABLES = {
    "run_feedback",
    "corpus_candidates",
    "candidate_reviews",
    "corpus_versions",
    "eval_samples",
    "judge_scores",
    "rubrics",
}


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_m36_adds_and_drops_learning_tables(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES

    command.downgrade(config, "0014")
    assert not (_TABLES & set(inspect(pg_engine).get_table_names()))

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES


def test_m36_migrations_are_idempotent(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _TABLES
