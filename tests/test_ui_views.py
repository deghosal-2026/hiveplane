"""Tests for operator UI view models (M22, #85)."""

from __future__ import annotations

from typing import Any

from hiveplane.ui.views import (
    build_cert_dashboard,
    build_fleet,
    build_run_detail,
    build_spend,
)

_RUN_STATES = ("queued", "running", "paused", "completed", "failed", "cancelled")


def _workload(
    name: str, *, status: str = "uncertified", team: str | None = "platform"
) -> dict[str, Any]:
    return {
        "name": name,
        "owner": "alice",
        "team": team,
        "runtime": "raw-worker",
        "certification_status": status,
        "current_version": 1,
        "updated_at": "2026-09-19T10:00:00Z",
        "last_run_at": "2026-09-19T11:00:00Z",
    }


def _run(run_id: str, workload: str, state: str) -> dict[str, Any]:
    return {"id": run_id, "workload_id": workload, "state": state}


# --------------------------------------------------------------------------- #
# Fleet
# --------------------------------------------------------------------------- #
def test_build_fleet_empty_inputs() -> None:
    view = build_fleet([], [], {"by_workload": [], "by_team": []})

    assert view.total_workloads == 0
    assert view.workloads == []
    assert view.total_spend_usd == 0.0
    assert view.status_counts == {
        "uncertified": 0,
        "provisional": 0,
        "certified": 0,
        "quarantined": 0,
    }


def test_build_fleet_aggregates_state_counts_and_failures() -> None:
    workloads = [
        _workload("agent-a", status="certified"),
        _workload("agent-b", status="quarantined", team=None),
    ]
    runs = [
        _run("r1", "agent-a", "completed"),
        _run("r2", "agent-a", "failed"),
        _run("r3", "agent-a", "cancelled"),
        _run("r4", "agent-b", "running"),
        _run("r5", "ghost", "failed"),
    ]

    view = build_fleet(workloads, runs, {"by_workload": [], "by_team": []})
    by_name = {row.name: row for row in view.workloads}

    assert view.total_workloads == 2
    assert view.status_counts == {
        "uncertified": 0,
        "provisional": 0,
        "certified": 1,
        "quarantined": 1,
    }
    assert by_name["agent-a"].state_counts == {
        "queued": 0,
        "running": 0,
        "paused": 0,
        "completed": 1,
        "failed": 1,
        "cancelled": 1,
    }
    assert by_name["agent-a"].recent_failures == 2
    assert by_name["agent-a"].last_run_at == "2026-09-19T11:00:00Z"
    assert by_name["agent-b"].state_counts["running"] == 1
    assert by_name["agent-b"].recent_failures == 0
    assert by_name["agent-b"].team is None


def test_build_fleet_joins_spend_by_workload_and_totals() -> None:
    workloads = [_workload("agent-a"), _workload("agent-b")]
    spend = {
        "by_workload": [
            {"workload": "agent-a", "team": "platform", "total_usd": 2.5, "run_count": 3}
        ],
        "by_team": [],
    }

    view = build_fleet(workloads, [], spend)
    by_name = {row.name: row for row in view.workloads}

    assert by_name["agent-a"].budget_burn_usd == 2.5
    assert by_name["agent-b"].budget_burn_usd == 0.0
    assert view.total_spend_usd == 2.5


# --------------------------------------------------------------------------- #
# Run detail
# --------------------------------------------------------------------------- #
def test_build_run_detail_extracts_header_and_preserves_timeline() -> None:
    story = {
        "run_id": "r1",
        "workload": "agent-a",
        "team": "platform",
        "state": "completed",
        "context": "production",
        "model_identity": "openai:gpt-4o",
        "cost_usd": 1.25,
        "sandbox": True,
        "sandbox_id": "sbx-1",
        "certification_status": "certified",
        "attestation_id": "att-1",
        "trace_id": "trace-1",
        "entries": [
            {"timestamp": "t1", "kind": "admission", "summary": "admitted", "detail": {}},
            {"timestamp": "t2", "kind": "tool_call", "summary": "search", "detail": {"x": 1}},
        ],
    }

    view = build_run_detail(story)

    assert view.run_id == "r1"
    assert view.state == "completed"
    assert view.model_identity == "openai:gpt-4o"
    assert view.cost_usd == 1.25
    assert view.sandbox is True
    assert view.trace_id == "trace-1"
    assert [entry.kind for entry in view.entries] == ["admission", "tool_call"]
    assert view.entries[1].detail == {"x": 1}


def test_build_run_detail_handles_empty_story() -> None:
    view = build_run_detail({})

    assert view.run_id == ""
    assert view.entries == []
    assert view.cost_usd == 0.0


# --------------------------------------------------------------------------- #
# Certification dashboard
# --------------------------------------------------------------------------- #
def _cert(
    record_id: str,
    workload: str,
    status: str,
    *,
    timestamp: str,
    pass_rate: float | None,
) -> dict[str, Any]:
    eval_summary = None if pass_rate is None else {"pass_rate": pass_rate}
    return {
        "record_id": record_id,
        "certification": {
            "certification_id": record_id,
            "workload_id": workload,
            "status": status,
            "target_context": "staging",
            "timestamp": timestamp,
            "attestation_id": f"att-{record_id}",
            "eval_summary": eval_summary,
        },
        "attestation": {"attestation_id": f"att-{record_id}"},
    }


def test_build_cert_dashboard_empty_inputs() -> None:
    view = build_cert_dashboard([])

    assert view.status_counts == {
        "uncertified": 0,
        "provisional": 0,
        "certified": 0,
        "quarantined": 0,
    }
    assert view.trends == []
    assert view.last_certified == []
    assert view.quarantine_history == []


def test_build_cert_dashboard_counts_trends_and_quarantine() -> None:
    records = [
        _cert("c1", "agent-a", "certified", timestamp="t1", pass_rate=0.8),
        _cert("c2", "agent-a", "quarantined", timestamp="t2", pass_rate=0.6),
        _cert("c3", "agent-b", "provisional", timestamp="t3", pass_rate=0.9),
    ]

    view = build_cert_dashboard(records)

    assert view.status_counts == {
        "uncertified": 0,
        "provisional": 1,
        "certified": 1,
        "quarantined": 1,
    }
    assert [(point.workload, point.timestamp, point.pass_rate) for point in view.trends] == [
        ("agent-a", "t1", 0.8),
        ("agent-a", "t2", 0.6),
        ("agent-b", "t3", 0.9),
    ]
    assert [(row.workload, row.timestamp, row.status) for row in view.last_certified] == [
        ("agent-a", "t2", "quarantined"),
        ("agent-b", "t3", "provisional"),
    ]
    assert [(row.workload, row.timestamp) for row in view.quarantine_history] == [
        ("agent-a", "t2")
    ]


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #
def test_build_spend_rollups_and_total() -> None:
    spend = {
        "by_workload": [
            {"workload": "agent-a", "team": "platform", "total_usd": 2.0, "run_count": 2},
            {"workload": "agent-b", "team": None, "total_usd": 1.0, "run_count": 1},
        ],
        "by_team": [
            {"team": "platform", "total_usd": 2.0, "run_count": 2},
            {"team": "ops", "total_usd": 1.0, "run_count": 1},
        ],
    }

    view = build_spend(spend)

    assert view.total_usd == 3.0
    assert [row.workload for row in view.by_workload] == ["agent-a", "agent-b"]
    assert [row.team for row in view.by_team] == ["platform", "ops"]


def test_build_spend_empty_inputs() -> None:
    view = build_spend({})

    assert view.total_usd == 0.0
    assert view.by_workload == []
    assert view.by_team == []
