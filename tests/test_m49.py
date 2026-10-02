"""Tests for cost showback, budget periods, and alerts (M49)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.core.workload import AgentWorkload
from hiveplane.cost import (
    BudgetScope,
    CarryRule,
    CostEvent,
    CostService,
    InMemoryCostStore,
    SpendCapExceededError,
    UnattributedUsageError,
    period_key,
    period_start,
)
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID, context_for_run

_NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
_T = TenantContext(tenant_id="t", role=Role.ADMIN)


def _service() -> CostService:
    return CostService(InMemoryCostStore(), clock=lambda: _NOW)


def _event(
    event_id: str,
    *,
    team_id: str = "team-a",
    workload_id: str = "agent-1",
    cost_usd: float = 1.0,
    saved_usd: float = 0.0,
    completed: bool = False,
    retry: bool = False,
    escalation: bool = False,
    occurred_at: datetime = _NOW,
    tenant_id: str = "t",
) -> CostEvent:
    return CostEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        team_id=team_id,
        workload_id=workload_id,
        cost_type=CostType.LLM,
        cost_usd=cost_usd,
        saved_usd=saved_usd,
        completed=completed,
        retry=retry,
        escalation=escalation,
        occurred_at=occurred_at,
    )


# --------------------------------------------------------------------------- #
# M49-01/02 — attribution
# --------------------------------------------------------------------------- #
def test_period_keys() -> None:
    assert period_key(CostPeriodKind.DAY, _NOW) == "2026-03-10"
    assert period_key(CostPeriodKind.WEEK, _NOW) == "2026-W11"
    assert period_key(CostPeriodKind.MONTH, _NOW) == "2026-03"
    assert period_start(CostPeriodKind.WEEK, _NOW).isoformat() == "2026-03-09"


def test_record_attributes_and_rejects_missing() -> None:
    service = _service()
    service.record(_event("e1", completed=True))

    bad = _event("e2", workload_id="")
    with pytest.raises(UnattributedUsageError):
        service.record(bad)
    report = service.showback("t", CostPeriodKind.DAY, ctx=_T)
    assert report.unattributed == 1


# --------------------------------------------------------------------------- #
# M49-03/04 — periods, carry, alerts
# --------------------------------------------------------------------------- #
def test_budget_spend_and_threshold_alerts_fire_once() -> None:
    service = _service()
    service.set_budget("t", BudgetScope.TEAM, "team-a", CostPeriodKind.DAY, limit_usd=10.0, ctx=_T)

    first = service.record(_event("e1", cost_usd=5.0))  # 50%
    assert [alert.threshold for alert in first] == [50]
    fired = service.record(_event("e2", cost_usd=3.5))  # 85%
    assert [alert.threshold for alert in fired] == [80]

    # already-fired thresholds do not fire again
    assert service.record(_event("e3", cost_usd=0.5)) == []
    period = service.periods("t", ctx=_T)[0]
    assert period.spent_usd == 9.0
    assert period.remaining_usd == 1.0


def test_rollover_carry_rules() -> None:
    service = _service()
    service.set_budget(
        "t", BudgetScope.TEAM, "team-a", CostPeriodKind.DAY, limit_usd=10.0,
        carry_rule=CarryRule.CAPPED, ctx=_T,
    )
    service.record(_event("e1", cost_usd=4.0))

    rolled = service.rollover("t", CostPeriodKind.DAY, ctx=_T)
    assert len(rolled) == 1
    assert rolled[0].period_key == "2026-03-11"
    assert rolled[0].carry_in_usd == 6.0  # min(remaining 6, limit 10)

    service.set_budget(
        "t", BudgetScope.TENANT, "t", CostPeriodKind.DAY, limit_usd=10.0,
        carry_rule=CarryRule.FULL, ctx=_T,
    )
    service.record(_event("e2", cost_usd=2.0))
    full = [
        p
        for p in service.rollover("t", CostPeriodKind.DAY, ctx=_T)
        if p.scope is BudgetScope.TENANT
    ]
    assert full[0].carry_in_usd == 8.0


def test_rollover_midweek_preserves_current_period_spend() -> None:
    service = _service()
    service.set_budget(
        "t", BudgetScope.TEAM, "team-a", CostPeriodKind.WEEK, limit_usd=100.0,
        carry_rule=CarryRule.FULL, ctx=_T,
    )
    service.record(_event("e1", cost_usd=90.0))

    rolled = service.rollover("t", CostPeriodKind.WEEK, ctx=_T)
    assert rolled[0].period_key == "2026-W12"
    current = [
        p for p in service.periods("t", ctx=_T) if p.period_key == "2026-W11"
    ]
    assert current[0].spent_usd == 90.0


def test_rollover_repeated_is_idempotent_and_keeps_next_period_spend() -> None:
    service = _service()
    service.set_budget(
        "t", BudgetScope.TEAM, "team-a", CostPeriodKind.WEEK, limit_usd=100.0,
        carry_rule=CarryRule.FULL, ctx=_T,
    )
    service.record(_event("e1", cost_usd=90.0))
    service.rollover("t", CostPeriodKind.WEEK, ctx=_T)
    # spend lands in the materialized next (W12) period
    service.record(
        _event("e2", cost_usd=5.0, occurred_at=datetime(2026, 3, 17, tzinfo=UTC))
    )

    rolled = service.rollover("t", CostPeriodKind.WEEK, ctx=_T)
    week12 = next(p for p in rolled if p.period_key == "2026-W12")
    assert week12.spent_usd == 5.0
    assert week12.carry_in_usd == 10.0

    # re-running materialization must not zero the next period's spend
    service.rollover("t", CostPeriodKind.WEEK, ctx=_T)
    again = next(
        p for p in service.periods("t", ctx=_T) if p.period_key == "2026-W12"
    )
    assert again.spent_usd == 5.0
    assert again.carry_in_usd == 10.0


def test_spend_cap_fails_closed() -> None:
    service = _service()
    service.set_budget(
        "t", BudgetScope.TENANT, "t", CostPeriodKind.MONTH, limit_usd=100.0,
        cap_usd=50.0, enforced=True, ctx=_T,
    )
    service.record(_event("e1", cost_usd=40.0))

    service.check_cap("t", CostPeriodKind.MONTH, projected_usd=5.0, ctx=_T)
    with pytest.raises(SpendCapExceededError):
        service.check_cap("t", CostPeriodKind.MONTH, projected_usd=20.0, ctx=_T)


# --------------------------------------------------------------------------- #
# M49-05 — cost-per-completed-task
# --------------------------------------------------------------------------- #
def test_cpct_includes_retries_escalations_and_cache_savings() -> None:
    service = _service()
    service.record(_event("e1", cost_usd=10.0, completed=True, saved_usd=2.0))
    service.record(_event("e2", cost_usd=4.0, retry=True))
    service.record(_event("e3", cost_usd=6.0, escalation=True))

    report = service.showback("t", CostPeriodKind.DAY, ctx=_T)
    assert report.total_cost_usd == 20.0
    assert report.total_completed_tasks == 1
    assert report.fleet_cpct == 20.0
    row = report.rows[0]
    assert row.completed_tasks == 1
    assert row.cost_per_completed_task == 20.0
    assert row.wasted_usd == 10.0  # retry + escalation
    assert report.cache_savings_usd == 2.0


def test_showback_group_by_workload_and_period_filter() -> None:
    service = _service()
    service.record(_event("e1", workload_id="agent-1", cost_usd=3.0, completed=True))
    service.record(_event("e2", workload_id="agent-2", cost_usd=5.0))
    # event in a different period is excluded
    service.record(
        _event("e3", cost_usd=99.0, occurred_at=datetime(2026, 4, 1, tzinfo=UTC))
    )

    report = service.showback("t", CostPeriodKind.DAY, group_by="workload", ctx=_T)
    assert {row.workload_id for row in report.rows} == {"agent-1", "agent-2"}
    assert report.total_cost_usd == 8.0


def test_showback_uses_period_bounded_query() -> None:
    from hiveplane.cost.store import InMemoryCostStore
    from hiveplane.tenancy.context import DEFAULT_CONTEXT

    class _CountingStore(InMemoryCostStore):
        def __init__(self) -> None:
            super().__init__()
            self.fetched = 0
            self.last_start: datetime | None = None
            self.last_end: datetime | None = None

        def list_events(
            self,
            tenant_id: str,
            *,
            start: datetime | None = None,
            end: datetime | None = None,
            ctx: object = DEFAULT_CONTEXT,
        ) -> list[CostEvent]:
            events = super().list_events(tenant_id, start=start, end=end, ctx=ctx)  # type: ignore[arg-type]
            self.fetched += len(events)
            self.last_start = start
            self.last_end = end
            return events

    store = _CountingStore()
    service = CostService(store, clock=lambda: _NOW)
    service.record(_event("in-period", cost_usd=1.0))
    service.record(
        _event("out-of-period", cost_usd=99.0, occurred_at=datetime(2026, 4, 1, tzinfo=UTC))
    )

    store.fetched = 0
    report = service.showback("t", CostPeriodKind.DAY, ctx=_T)

    assert store.fetched == 1
    assert store.last_start is not None and store.last_end is not None
    assert report.total_cost_usd == 1.0


# --------------------------------------------------------------------------- #
# M50 production surface
# --------------------------------------------------------------------------- #


def test_budget_period_alerts_operable_in_deployed_app() -> None:
    """A deployed app exposes budget configuration, periods, and alerts."""
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    app = create_app()
    client = TestClient(app)

    created = client.post(
        "/cost/budgets",
        json={
            "scope": "tenant",
            "scope_id": DEFAULT_TENANT_ID,
            "kind": "day",
            "limit_usd": 10.0,
            "cap_usd": 8.0,
            "enforced": True,
        },
    )
    assert created.status_code == 200

    now = datetime.now(UTC)
    app.state.cost_service.record(
        CostEvent(
            event_id="e1",
            tenant_id=DEFAULT_TENANT_ID,
            team_id="platform",
            workload_id="agent-1",
            cost_type=CostType.LLM,
            cost_usd=9.0,
            occurred_at=now,
        ),
        ctx=TenantContext(tenant_id=DEFAULT_TENANT_ID, role=Role.ADMIN),
    )

    periods = client.get("/cost/periods").json()
    assert periods and periods[0]["spent_usd"] == 9.0
    assert periods[0]["cap_usd"] == 8.0 and periods[0]["enforced"] is True

    alerts = client.get("/cost/alerts").json()
    assert {alert["threshold"] for alert in alerts} == {50, 80}


def test_showback_counts_completed_runs_end_to_end(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed production run flips its cost event to completed (#549)."""
    from hiveplane.api.app import create_app
    from hiveplane.certification.models import CertificationStatus
    from hiveplane.core.run import AdmissionContext, RunState
    from hiveplane.core.usage import UsageReport
    from hiveplane.registry.models import AdmissionDecision

    app = create_app()
    registry = app.state.registry_service
    workload = make_manifest(name="agent-1")
    registry.create(workload)

    def _admit(name: str, context: object, *, ctx: object = None) -> AdmissionDecision:
        return AdmissionDecision(
            workload=name,
            context=AdmissionContext.PRODUCTION,
            admitted=True,
            actual_status=CertificationStatus.CERTIFIED,
        )

    monkeypatch.setattr(registry, "check_admission", _admit)

    run_service = app.state.run_service
    ctx = context_for_run(DEFAULT_TENANT_ID)
    run = run_service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        ctx=ctx,
    )
    run_service.record_usage(
        run.id,
        UsageReport(
            run_id=run.id,
            input_tokens=10_000,
            output_tokens=0,
            tool_calls=1,
            cost_usd=0.05,
            timestamp=_NOW,
            model_identity="openai/gpt-4o/2024-08-06",
        ),
        ctx=ctx,
    )
    run_service.transition(run.id, RunState.RUNNING, actor="adapter", ctx=ctx)
    run_service.transition(run.id, RunState.COMPLETED, actor="adapter", ctx=ctx)

    report = app.state.cost_service.showback(
        DEFAULT_TENANT_ID,
        CostPeriodKind.DAY,
        at=_NOW,
        ctx=TenantContext(tenant_id=DEFAULT_TENANT_ID, role=Role.ADMIN),
    )

    assert report.total_completed_tasks == 1
    assert report.fleet_cpct > 0.0


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_postgres_cost_store_round_trip(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.cost.store import PostgresCostStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresCostStore(pg_engine)
    store.clear()

    service = CostService(store, clock=lambda: _NOW)
    service.set_budget("t", BudgetScope.TEAM, "team-a", CostPeriodKind.DAY, limit_usd=10.0, ctx=_T)
    fired = service.record(_event("e1", cost_usd=8.0))
    assert [a.threshold for a in fired] == [50, 80]

    reopened = PostgresCostStore(pg_engine)
    assert reopened.get_period(
        service.periods("t", ctx=_T)[0].period_id, ctx=_T
    ) is not None
    assert reopened.list_periods("t", ctx=_T)[0].spent_usd == 8.0
    assert reopened.list_events("t", ctx=_T)[0].event_id == "e1"
    assert reopened.list_alerts("t", ctx=_T)
    assert reopened.unattributed() == 0
    with pytest.raises(UnattributedUsageError):
        service.record(_event("e2", workload_id=""))
    assert reopened.unattributed() == 1
    store.clear()


def test_cost_showback_api(pg_engine: object) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    response = client.get(
        "/cost/showback",
        params={"tenant_id": "t", "period": "day"},
        headers={"X-Hiveplane-Tenant": "t"},
    )
    assert response.status_code == 200
    assert response.json()["unattributed"] == 0
    assert client.get(
        "/cost/showback/t/team-a",
        params={"period": "month"},
        headers={"X-Hiveplane-Tenant": "t"},
    ).status_code == 200
    assert client.get("/cost/showback", params={"period": "bogus"}).status_code == 422
