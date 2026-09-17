"""Tests for the persistence schema (no database required)."""

from __future__ import annotations

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
