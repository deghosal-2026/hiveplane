"""Tests for digest scheduling and per-team routing (M57-02)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.service import CostService
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.delivery.models import (
    DeliveryChannel,
    DeliveryDestination,
    DeliveryStatus,
    NotificationPreference,
)
from hiveplane.delivery.service import DeliveryService
from hiveplane.delivery.store import InMemoryDeliveryStore
from hiveplane.drift.store import InMemoryDriftStore
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.models import ReportSchedule
from hiveplane.reporting.scheduler import DigestScheduler
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy.context import SYSTEM_CONTEXT, context_for_run

_NOW = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)  # Wednesday of ISO week 11
_NEXT_DAILY = datetime(2026, 3, 12, 8, 0, tzinfo=UTC)
_NEXT_MONDAY = datetime(2026, 3, 16, 8, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[DeliveryChannel, str, dict[str, object]]] = []

    def send(self, channel: DeliveryChannel, target: str, payload: dict[str, object]) -> None:
        self.sent.append((channel, target, payload))


@dataclass
class _Rig:
    scheduler: DigestScheduler
    reporting: InMemoryReportingStore
    digest: DigestService
    delivery: DeliveryService
    sender: _Recorder
    audit: InMemoryAuditLog
    clock: _Clock


def _rig(
    make_manifest: Callable[..., AgentWorkload], sender: _Recorder | None = None
) -> _Rig:
    clock = _Clock(_NOW)
    cost = CostService(InMemoryCostStore(), clock=clock)
    drift = InMemoryDriftStore()
    approvals = ApprovalService(InMemoryApprovalStore(), clock=clock)
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="w1"))
    reporting = InMemoryReportingStore()
    digest = DigestService(reporting, cost, drift, approvals, registry, clock=clock)
    sender = sender or _Recorder()
    delivery = DeliveryService(InMemoryDeliveryStore(), sender, clock=clock)
    audit = InMemoryAuditLog(clock=clock)
    scheduler = DigestScheduler(reporting, digest, delivery, audit=audit, clock=clock)
    return _Rig(scheduler, reporting, digest, delivery, sender, audit, clock)


def _actions(rig: _Rig) -> set[str]:
    return {record.action for record in rig.audit.records(ctx=context_for_run("default"))}


def test_schedule_persists_and_computes_next_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)

    schedule = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * 1",
        period_kind=CostPeriodKind.WEEK,
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )

    assert schedule.tenant_id == "default"
    assert schedule.team_id == "team-a"
    assert schedule.channels == [DeliveryChannel.SLACK]
    assert schedule.next_run == _NEXT_MONDAY

    stored = rig.reporting.get_schedule(
        schedule.schedule_id, tenant_id="default", ctx=context_for_run("default")
    )
    assert stored is not None
    assert stored.schedule_id == schedule.schedule_id

    again = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * 1",
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )
    assert again.schedule_id == schedule.schedule_id


def test_due_returns_only_matured_schedules(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    schedule = rig.scheduler.schedule(
        "default", cron="0 8 * * *", channels=[], ctx=context_for_run("default")
    )
    assert schedule.next_run == _NEXT_DAILY

    assert rig.scheduler.due(at=_NOW, ctx=context_for_run("default")) == []

    matured = rig.scheduler.due(at=schedule.next_run, ctx=context_for_run("default"))
    assert [item.schedule_id for item in matured] == [schedule.schedule_id]


def test_run_due_delivers_markdown_and_advances(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="default",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        )
    )
    schedule = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * *",
        period_kind=CostPeriodKind.WEEK,
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert len(attempts) == 1
    assert attempts[0].status is DeliveryStatus.DELIVERED
    assert attempts[0].channel is DeliveryChannel.SLACK
    assert rig.sender.sent[0][1] == "#ops"
    assert "## Spend" in str(rig.sender.sent[0][2])

    reports = rig.reporting.list_reports(
        tenant_id="default", ctx=context_for_run("default")
    )
    assert len(reports) == 1

    advanced = rig.reporting.get_schedule(
        schedule.schedule_id, tenant_id="default", ctx=context_for_run("default")
    )
    assert advanced is not None
    assert advanced.next_run == _NEXT_DAILY + timedelta(days=1)
    assert "report.digest.delivered" in _actions(rig)


def test_run_due_concurrent_delivers_once(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    """A re-entrant sweep over the same matured schedule must not double-deliver."""
    state: dict[str, object] = {"fired": False}

    class _ReentrantSender(_Recorder):
        def send(
            self, channel: DeliveryChannel, target: str, payload: dict[str, object]
        ) -> None:
            super().send(channel, target, payload)
            if not state["fired"]:
                state["fired"] = True
                scheduler = state["scheduler"]
                assert isinstance(scheduler, DigestScheduler)
                scheduler.run_due(
                    at=state["at"],  # type: ignore[arg-type]
                    ctx=context_for_run("default"),
                )

    rig = _rig(make_manifest, sender=_ReentrantSender())
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="default",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        )
    )
    schedule = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * *",
        period_kind=CostPeriodKind.WEEK,
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )
    state["scheduler"] = rig.scheduler
    state["at"] = schedule.next_run

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert len(attempts) == 1
    assert len(rig.sender.sent) == 1


def test_run_due_skips_without_destinations_and_advances(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    schedule = rig.scheduler.schedule(
        "default", team_id="team-a", cron="0 8 * * *", channels=[], ctx=context_for_run("default")
    )

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert attempts == []
    assert rig.sender.sent == []
    advanced = rig.reporting.get_schedule(
        schedule.schedule_id, tenant_id="default", ctx=context_for_run("default")
    )
    assert advanced is not None
    assert advanced.next_run == _NEXT_DAILY + timedelta(days=1)
    assert "report.digest.skipped" in _actions(rig)


def test_run_due_honors_preference_channel_suppression(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="default",
            channels=[DeliveryChannel.EMAIL],
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        )
    )
    schedule = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * *",
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert len(attempts) == 1
    assert attempts[0].status is DeliveryStatus.SUPPRESSED
    assert rig.sender.sent == []


def test_scheduler_never_crosses_tenants(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.scheduler.schedule("beta", cron="0 8 * * *", channels=[], ctx=context_for_run("beta"))

    far_future = datetime(2027, 1, 1, tzinfo=UTC)
    assert rig.scheduler.due(at=far_future, ctx=context_for_run("default")) == []
    beta = rig.scheduler.due(at=far_future, ctx=context_for_run("beta"))
    assert [item.tenant_id for item in beta] == ["beta"]


def test_bind_audit_after_construction(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    scheduler = DigestScheduler(rig.reporting, rig.digest, rig.delivery, clock=rig.clock)
    scheduler.bind_audit(rig.audit)
    schedule = scheduler.schedule(
        "default", cron="0 8 * * *", channels=[], ctx=context_for_run("default")
    )

    scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert "report.digest.skipped" in _actions(rig)


def test_run_due_without_audit_is_silent(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    scheduler = DigestScheduler(rig.reporting, rig.digest, rig.delivery, clock=rig.clock)
    schedule = scheduler.schedule(
        "default", cron="0 8 * * *", channels=[], ctx=context_for_run("default")
    )

    assert scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default")) == []
    assert rig.audit.records(ctx=context_for_run("default")) == []


def test_run_due_ignores_cross_tenant_preference_for_shared_team_id(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="beta",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#beta")],
        ),
        ctx=context_for_run("beta"),
    )
    schedule = rig.scheduler.schedule(
        "default",
        team_id="team-a",
        cron="0 8 * * *",
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("default"),
    )

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("default"))

    assert attempts == []
    assert rig.sender.sent == []
    assert "report.digest.skipped" in _actions(rig)


def test_run_due_routes_to_same_tenant_for_non_default_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="beta",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#beta")],
        ),
        ctx=context_for_run("beta"),
    )
    schedule = rig.scheduler.schedule(
        "beta",
        team_id="team-a",
        cron="0 8 * * *",
        channels=[DeliveryChannel.SLACK],
        ctx=context_for_run("beta"),
    )

    attempts = rig.scheduler.run_due(at=schedule.next_run, ctx=context_for_run("beta"))

    assert len(attempts) == 1
    assert attempts[0].status is DeliveryStatus.DELIVERED
    assert rig.sender.sent[0][1] == "#beta"


def _matured_schedule(tenant_id: str) -> ReportSchedule:
    return ReportSchedule(
        schedule_id=f"sched-{tenant_id}",
        tenant_id=tenant_id,
        team_id="team-a",
        cron="0 8 * * *",
        period_kind=CostPeriodKind.WEEK,
        active=True,
        next_run=_NOW,
        created_at=_NOW - timedelta(days=1),
    )


def test_run_all_due_system_context_sweeps_every_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    for tenant, target in (("default", "#default"), ("beta", "#beta")):
        rig.delivery.set_preference(
            NotificationPreference(
                team_id="team-a",
                tenant_id=tenant,
                destinations=[
                    DeliveryDestination(channel=DeliveryChannel.SLACK, target=target)
                ],
            ),
            ctx=context_for_run(tenant),
        )
        rig.reporting.save_schedule(_matured_schedule(tenant), ctx=context_for_run(tenant))

    attempts = rig.scheduler.run_all_due(at=_NOW, ctx=SYSTEM_CONTEXT)

    assert sorted(attempt.tenant_id for attempt in attempts) == ["beta", "default"]
    assert {entry[1] for entry in rig.sender.sent} == {"#default", "#beta"}


def test_run_all_due_tenant_context_is_scoped(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    rig.delivery.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="default",
            destinations=[
                DeliveryDestination(channel=DeliveryChannel.SLACK, target="#default")
            ],
        )
    )
    rig.reporting.save_schedule(_matured_schedule("default"), ctx=context_for_run("default"))
    rig.reporting.save_schedule(_matured_schedule("beta"), ctx=context_for_run("beta"))

    attempts = rig.scheduler.run_all_due(at=_NOW, ctx=context_for_run("default"))

    assert [attempt.tenant_id for attempt in attempts] == ["default"]
    beta = rig.reporting.list_schedules(ctx=context_for_run("beta"))
    assert beta[0].next_run == _NOW
