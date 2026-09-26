"""Tests for the persistence schema (no database required)."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from hiveplane.core.run import Run
from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base, create_engine_from_settings


def _index_names(table: str) -> set[str]:
    return {
        str(index.name)
        for index in Base.metadata.tables[table].indexes
        if index.name is not None
    }


def test_all_design_tables_exist() -> None:
    expected = {
        "workloads",
        "workload_versions",
        "runs",
        "run_events",
        "usage_events",
        "audit_log",
        "approvals",
        "certifications",
        "attestations",
        "tools",
        "trigger_rules",
        "drift_schedules",
        "fan_out_deliveries",
        "health_signals",
        "cost_attributions",
        "run_admissions",
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
        "reconcile_runs",
        "trigger_nonces",
        "trigger_freezes",
        "pipeline_run_headers",
        "pipeline_node_runs",
    }
    assert expected <= set(Base.metadata.tables)


def test_run_query_indexes_exist() -> None:
    assert "ix_runs_workload_state_created" in _index_names("runs")
    assert "ix_run_events_run_timestamp" in _index_names("run_events")
    assert "ix_usage_events_run_timestamp" in _index_names("usage_events")
    assert "ix_fan_out_deliveries_run_status" in _index_names("fan_out_deliveries")
    assert "ix_audit_log_subject" in _index_names("audit_log")


def test_engine_is_created_from_settings() -> None:
    engine = create_engine_from_settings()
    assert engine.url.drivername == "postgresql+psycopg"
    engine.dispose()


def test_every_table_is_tenant_scoped() -> None:
    exempt = {"tenants", "teams", "memberships"}
    missing = {
        name
        for name in Base.metadata.tables
        if name not in exempt
        and "tenant_id" not in Base.metadata.tables[name].columns
    }
    assert missing == set()


def test_tenant_qualified_uniques_include_tenant_id() -> None:
    uniques = {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in Base.metadata.tables["teams"].constraints
        if isinstance(constraint, UniqueConstraint)
        and isinstance(constraint.name, str)
        and constraint.name.startswith("uq_")
    }
    assert uniques["uq_teams_tenant_name"] == {"tenant_id", "name"}
    assert uniques["uq_teams_tenant_attribution"] == {"tenant_id", "attribution_key"}


def test_run_carries_tenant_attribution() -> None:
    assert {"tenant_id", "team_id", "attribution_key"} <= set(Run.model_fields)
