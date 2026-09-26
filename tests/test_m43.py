"""Tests for synthetic probes, approval analytics, and plane self-monitoring (M43)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.health.analytics import approval_analytics
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.probes.models import ProbeOutcome, ProbeSpec, ProbeStatus
from hiveplane.probes.service import ProbeBudgetExceededError, ProbeService

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Synthetic probes (M43-01, M43-02, M43-03)
# --------------------------------------------------------------------------- #
class _Runner:
    def __init__(self, *, passed: bool = True, cost: float = 0.01, latency: int = 50) -> None:
        self.calls: list[ProbeSpec] = []
        self.fanout_calls = 0
        self._outcome = ProbeOutcome(
            passed=passed, latency_ms=latency, cost_usd=cost, detail={"checked": True}
        )

    def run(self, spec: ProbeSpec) -> ProbeOutcome:
        self.calls.append(spec)
        return self._outcome


def _service(
    runner: _Runner | None = None, *, budget_cap: float | None = None
) -> tuple[ProbeService, _Runner]:
    runner = runner or _Runner()
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"probe-{counter['n']}"

    return (
        ProbeService(
            runner=runner,
            budget_cap_usd=budget_cap,
            clock=lambda: _NOW,
            id_factory=_id,
        ),
        runner,
    )


def _spec(workload: str = "repo-agent") -> ProbeSpec:
    return ProbeSpec(
        workload_id=workload,
        input={"ping": "health"},
        expected="ok",
        interval_seconds=300,
    )


def test_probe_run_records_pass_and_evidence() -> None:
    service, runner = _service()

    result = service.run(_spec())

    assert result.status is ProbeStatus.PASSED
    assert result.passed is True
    assert result.latency_ms == 50
    assert result.cost_usd == 0.01
    assert runner.calls[0].workload_id == "repo-agent"


def test_failing_probe_raises_an_early_drift_warning() -> None:
    service, _ = _service(_Runner(passed=False))

    result = service.run(_spec())

    assert result.status is ProbeStatus.FAILED
    assert service.degraded("repo-agent") is True
    assert result.warning is not None
    assert result.warning.rule_id == "probe.decay"


def test_probe_uses_a_separate_capped_budget() -> None:
    service, _ = _service(_Runner(cost=0.6), budget_cap=1.0)
    service.run(_spec())
    assert service.probe_spend("repo-agent") == 0.6

    try:
        service.run(_spec())
    except ProbeBudgetExceededError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ProbeBudgetExceededError")


def test_probe_never_delivers_to_fanout() -> None:
    service, runner = _service()
    service.run(_spec())

    assert runner.fanout_calls == 0
    assert service.list_results("repo-agent")[0].tag == "probe"


def test_probe_schedule_reports_due_workloads() -> None:
    service, _ = _service()
    service.schedule(_spec())

    assert [schedule.workload_id for schedule in service.due()] == ["repo-agent"]
    service.run(_spec())
    assert service.due() == []


def test_probe_schedule_advances_after_a_run() -> None:
    service, _ = _service()
    service.schedule(_spec())
    service.run(_spec())

    schedule = service.schedules()[0]
    assert schedule.next_run == _NOW + timedelta(seconds=300)


def test_probe_degraded_is_false_when_all_pass() -> None:
    service, _ = _service()

    service.run(_spec())

    assert service.degraded("repo-agent") is False


# --------------------------------------------------------------------------- #
# Approval analytics (M43-05)
# --------------------------------------------------------------------------- #
def _approval(
    approval_id: str,
    *,
    approver: str | None,
    status: ApprovalStatus,
    wait_seconds: int = 0,
) -> ApprovalRecord:
    decided = (
        _NOW + timedelta(seconds=wait_seconds)
        if status is not ApprovalStatus.PENDING
        else None
    )
    return ApprovalRecord(
        approval_id=approval_id,
        run_id=f"run-{approval_id}",
        workload="repo-agent",
        rule="trust.destructive",
        reason="needs approval",
        requested_at=_NOW,
        status=status,
        decided_at=decided,
        decided_by=approver,
    )


def test_approval_analytics_computes_latency_by_approver() -> None:
    records = [
        _approval("a1", approver="alice", status=ApprovalStatus.APPROVED, wait_seconds=100),
        _approval("a2", approver="alice", status=ApprovalStatus.APPROVED, wait_seconds=200),
        _approval("a3", approver="bob", status=ApprovalStatus.DENIED, wait_seconds=10),
        _approval("a4", approver=None, status=ApprovalStatus.PENDING),
    ]

    report = approval_analytics(records)

    assert report.pending == 1
    assert report.decided == 3
    assert report.by_approver["alice"] == 150.0
    assert report.by_approver["bob"] == 10.0
    assert report.bottleneck == "alice"


def test_approval_analytics_counts_approve_and_reject_trends() -> None:
    records = [
        _approval("a1", approver="alice", status=ApprovalStatus.APPROVED),
        _approval("a2", approver="bob", status=ApprovalStatus.DENIED),
    ]

    report = approval_analytics(records)

    assert report.approved == 1
    assert report.denied == 1


def test_approval_analytics_empty_state() -> None:
    report = approval_analytics([])

    assert report.pending == 0
    assert report.bottleneck is None


# --------------------------------------------------------------------------- #
# Plane self-monitoring (M43-06)
# --------------------------------------------------------------------------- #
def test_plane_metrics_render_prometheus_text() -> None:
    metrics = PlaneMetrics()
    metrics.increment("hiveplane_runs_submitted_total", workload="repo-agent")
    metrics.increment("hiveplane_runs_submitted_total", workload="repo-agent")
    metrics.set_gauge("hiveplane_queue_depth", 3)

    text = metrics.render()

    assert 'hiveplane_runs_submitted_total{workload="repo-agent"} 2' in text
    assert "hiveplane_queue_depth 3" in text


def test_plane_metrics_is_decoupled_and_deterministic() -> None:
    metrics = PlaneMetrics()
    metrics.increment("hiveplane_guard_trips_total", guard="context")

    assert metrics.render() == metrics.render()


# --------------------------------------------------------------------------- #
# Grafana dashboards (M43-07)
# --------------------------------------------------------------------------- #
def test_grafana_dashboards_are_shipped_and_valid() -> None:
    root = Path(__file__).resolve().parents[1] / "deploy" / "grafana" / "dashboards"
    for name in ("fleet.json", "health.json", "cost.json", "plane.json"):
        document = json.loads((root / name).read_text(encoding="utf-8"))
        assert document["title"]
        assert isinstance(document["panels"], list)
        assert document["panels"]


# --------------------------------------------------------------------------- #
# API (M43-04/05/06/07)
# --------------------------------------------------------------------------- #
def test_health_probes_analytics_and_metrics_endpoints() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.core.approval import ApprovalStatus
    from hiveplane.policy.approvals import ApprovalService
    from hiveplane.policy.store import InMemoryApprovalStore

    app = create_app()
    approval_store = InMemoryApprovalStore()
    request_time = {"t": _NOW}
    approvals = ApprovalService(approval_store, clock=lambda: request_time["t"])
    approval = approvals.request(
        run_id="run-1", workload="repo-agent", rule="trust.destructive", reason="needs approval"
    )
    request_time["t"] = _NOW + timedelta(seconds=120)
    approvals.decide(approval.approval_id, status=ApprovalStatus.APPROVED, operator="alice")
    app.state.approval_service = approvals
    client = TestClient(app)

    probes = client.get("/health/probes")
    analytics = client.get("/analytics/approvals")
    metrics = client.get("/metrics")

    assert probes.status_code == 200
    assert analytics.status_code == 200
    assert analytics.json()["by_approver"]["alice"] == 120.0
    assert metrics.status_code == 200
    assert "hiveplane_up 1" in metrics.text
