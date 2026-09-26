"""Migration 0003 tenancy backfill and idempotency tests (Postgres-gated)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_ROOT / "src/hiveplane/persistence/migrations")
    )
    return config


def test_0003_is_idempotent_from_head(pg_engine: Engine) -> None:
    config = _config()
    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    tables = set(inspect(pg_engine).get_table_names())
    assert {"tenants", "teams", "memberships"} <= tables
    assert "tenant_id" in {c["name"] for c in inspect(pg_engine).get_columns("runs")}


def test_backfill_seeds_default_tenant_and_team(pg_engine: Engine) -> None:
    command.upgrade(_config(), "head")
    with pg_engine.connect() as conn:
        tenant_ids = set(conn.execute(text("SELECT tenant_id FROM tenants")).scalars())
        team_ids = set(conn.execute(text("SELECT team_id FROM teams")).scalars())
    assert {"default", "system"} <= tenant_ids
    assert "default" in team_ids


def test_legacy_run_row_backfills_to_default_tenant(pg_engine: Engine) -> None:
    config = _config()
    command.downgrade(config, "0002")
    with pg_engine.begin() as conn:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO workloads (name, owner, team, certification_status, "
                "current_version, created_at, updated_at, payload) VALUES "
                "('agent-1', 'alice', 'platform', 'certified', 1, :now, :now, '{}') "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO runs (id, workload_id, caller, state, sandbox, cost_usd, "
                "created_at, updated_at, payload) VALUES "
                "('legacy-run', 'agent-1', 'alice', 'completed', false, 0, :now, :now, '{}') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"now": now},
        )
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        run_tenant = conn.execute(
            text("SELECT tenant_id FROM runs WHERE id = 'legacy-run'")
        ).scalar_one()
        workload_tenant = conn.execute(
            text("SELECT tenant_id FROM workloads WHERE name = 'agent-1'")
        ).scalar_one()
    assert run_tenant == "default"
    assert workload_tenant == "default"


def test_orphan_runs_are_deleted_before_fk(pg_engine: Engine) -> None:
    config = _config()
    command.downgrade(config, "0002")
    with pg_engine.begin() as conn:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO runs (id, workload_id, caller, state, sandbox, cost_usd, "
                "created_at, updated_at, payload) VALUES "
                "('orphan', 'ghost', 'alice', 'completed', false, 0, :now, :now, '{}') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"now": now},
        )
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM runs WHERE id = 'orphan'")
        ).scalar_one()
    assert remaining == 0


def test_orphan_workload_refs_are_deleted_before_fk(pg_engine: Engine) -> None:
    config = _config()
    command.downgrade(config, "0002")
    with pg_engine.begin() as conn:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO workloads (name, owner, team, certification_status, "
                "current_version, created_at, updated_at, payload) VALUES "
                "('agent-1', 'alice', 'platform', 'certified', 1, :now, :now, '{}') "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO certifications (certification_id, workload, status, "
                "created_at, payload) VALUES "
                "('cert-orphan', 'ghost', 'certified', :now, '{}') "
                "ON CONFLICT (certification_id) DO NOTHING"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO attestations (attestation_id, workload, model_identity, "
                "created_at, payload) VALUES "
                "('att-orphan', 'ghost', 'gpt-4o', :now, '{}') "
                "ON CONFLICT (attestation_id) DO NOTHING"
            ),
            {"now": now},
        )
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        certifications = conn.execute(
            text("SELECT count(*) FROM certifications WHERE certification_id = 'cert-orphan'")
        ).scalar_one()
        attestations = conn.execute(
            text("SELECT count(*) FROM attestations WHERE attestation_id = 'att-orphan'")
        ).scalar_one()
    assert certifications == 0
    assert attestations == 0
