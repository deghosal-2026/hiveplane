"""Migration upgrade/downgrade and index-usage tests (Postgres-gated)."""

from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    if output is not None:
        config.output_buffer = output
    return config


def test_offline_upgrade_emits_schema_sql() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output), "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE runs" in sql
    assert "CREATE TABLE audit_log" in sql
    assert "ix_runs_workload_state_created" in sql


def test_upgrade_downgrade_upgrade(pg_engine: Engine) -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    tables = set(inspect(pg_engine).get_table_names())
    assert {"runs", "run_events", "usage_events", "audit_log", "fan_out_deliveries"} <= tables

    command.downgrade(config, "base")
    assert "runs" not in set(inspect(pg_engine).get_table_names())

    command.upgrade(config, "head")
    assert "runs" in set(inspect(pg_engine).get_table_names())


def test_run_fleet_query_uses_index(pg_engine: Engine) -> None:
    plan = (
        pg_engine.connect()
        .execute(
            text(
                "EXPLAIN SELECT id FROM runs "
                "WHERE workload_id = 'agent-1' AND state = 'running' "
                "ORDER BY created_at"
            )
        )
        .scalars()
        .all()
    )
    assert "ix_runs_workload_state_created" in "\n".join(plan)
