"""Fleet-control migration 0004 upgrade/downgrade tests (Postgres-gated)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

_ROOT = Path(__file__).resolve().parents[1]

_FLEET_TABLES = {
    "triggers",
    "trigger_events",
    "trigger_runs",
    "trigger_dlq",
    "pipelines",
    "pipeline_runs",
    "policy_pack_versions",
    "policy_decisions",
    "secrets",
    "secret_refs",
    "workers",
    "worker_leases",
    "worker_heartbeats",
    "retention_policies",
    "artifacts",
    "metering_events",
    "cost_periods",
    "desired_specs",
    "reconcile_state",
    "drift_records",
}


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0004_upgrades_and_downgrades(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _FLEET_TABLES

    command.downgrade(config, "0003")
    assert not (_FLEET_TABLES & set(inspect(pg_engine).get_table_names()))

    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _FLEET_TABLES


def test_0004_is_idempotent(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) >= _FLEET_TABLES
