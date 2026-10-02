"""Unit tests for the weekly fleet digest (M57-01)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.models import CostEvent
from hiveplane.cost.service import CostService
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.drift.models import (
    DriftAssessment,
    DriftVerdict,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.drift.store import InMemoryDriftStore
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.models import DigestContent, ReportKind
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy.context import DEFAULT_CONTEXT, context_for_run

_NOW = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)  # Wednesday of ISO week 11


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@dataclass
class _Rig:
    service: DigestService
    cost: CostService
    drift: InMemoryDriftStore
    approvals: ApprovalService
    registry: RegistryService
    reporting: InMemoryReportingStore
    clock: _Clock


def _rig(make_manifest: Callable[..., AgentWorkload]) -> _Rig:
    clock = _Clock(_NOW)
    cost = CostService(InMemoryCostStore(), clock=clock)
    drift = InMemoryDriftStore()
    approvals = ApprovalService(InMemoryApprovalStore(), clock=clock)
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="w1"))
    registry.create(make_manifest(name="w2"))
    reporting = InMemoryReportingStore()
    service = DigestService(reporting, cost, drift, approvals, registry, clock=clock)
    return _Rig(service, cost, drift, approvals, registry, reporting, clock)


def _assessment(
    workload: str, verdict: DriftVerdict, *, timestamp: datetime = _NOW
) -> DriftAssessment:
    return DriftAssessment(
        workload=workload,
        verdict=verdict,
        pass_rate_before=0.9,
        pass_rate_after=0.5,
        pass_rate_delta=-0.4,
        failed_before=1,
        failed_after=5,
        new_failures=4,
        critical_failures=0,
        threshold_pass_rate=0.8,
        max_new_failures=2,
        required_consecutive_failures=1,
        consecutive_failures=1,
        exceeded=True,
        strong_signal=False,
        should_quarantine=True,
        reason="drifted",
        timestamp=timestamp,
    )


def _seed_default(rig: _Rig) -> None:
    for event_id, workload, cost, completed in (
        ("e1", "w1", 5.0, True),
        ("e2", "w2", 20.0, False),
    ):
        rig.cost.record(
            CostEvent(
                event_id=event_id,
                tenant_id="default",
                team_id="team-a",
                workload_id=workload,
                cost_type=CostType.LLM,
                cost_usd=cost,
                completed=completed,
                occurred_at=_NOW,
            )
        )
    rig.drift.add_quarantine(
        QuarantineRecord(
            quarantine_id="q-active",
            workload="w1",
            reason="drift",
            timestamp=_NOW,
        ),
        ctx=DEFAULT_CONTEXT,
    )
    rig.drift.add_quarantine(
        QuarantineRecord(
            quarantine_id="q-reinstated",
            workload="w2",
            reason="drift",
            status=QuarantineStatus.REINSTATED,
            timestamp=_NOW - timedelta(days=30),
            reinstated_at=_NOW,
            reinstated_by="operator",
        ),
        ctx=DEFAULT_CONTEXT,
    )
    rig.drift.add_assessment(_assessment("w1", DriftVerdict.DRIFTED), ctx=DEFAULT_CONTEXT)
    rig.drift.add_assessment(
        _assessment("w1", DriftVerdict.STABLE, timestamp=_NOW + timedelta(hours=1)),
        ctx=DEFAULT_CONTEXT,
    )
    rig.drift.add_assessment(_assessment("w2", DriftVerdict.STABLE), ctx=DEFAULT_CONTEXT)
    rig.drift.add_assessment(
        _assessment("w2", DriftVerdict.DRIFTED, timestamp=_NOW - timedelta(days=30)),
        ctx=DEFAULT_CONTEXT,
    )

    first = rig.approvals.request(run_id="r1", workload="w1", rule="rule", reason="escalated")
    rig.clock.advance(30)
    rig.approvals.decide(first.approval_id, status=ApprovalStatus.APPROVED, operator="alice")
    rig.clock.advance(30)
    second = rig.approvals.request(run_id="r2", workload="w2", rule="rule", reason="escalated")
    rig.clock.advance(60)
    rig.approvals.decide(second.approval_id, status=ApprovalStatus.DENIED, operator="bob")
    rig.clock.now = _NOW


def test_digest_aggregates_seeded_data(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)

    content = rig.service.build(context_for_run("default"), kind=CostPeriodKind.WEEK, at=_NOW)

    assert content.tenant_id == "default"
    assert content.period_key == "2026-W11"
    assert content.period_start == date(2026, 3, 9)
    assert content.period_end == date(2026, 3, 15)
    assert content.generated_at == _NOW

    assert content.spend.group_by == "team"
    assert content.spend.total_cost_usd == 25.0
    assert content.spend.total_completed_tasks == 1
    assert content.roi.total_spend_usd == 25.0

    assert content.drift.quarantined == 1
    assert content.drift.reinstated == 1
    assert content.drift.active == 1
    assert content.drift.drifting == ["w1"]

    assert content.approvals.total == 2
    assert content.approvals.approved == 1
    assert content.approvals.denied == 1
    assert content.approvals.mean_latency_seconds == 45.0
    assert content.approvals.bottleneck == "bob"


def test_drifting_uses_any_in_period_assessment(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)

    content = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)

    # w1 drifted earlier in the period (its latest assessment is STABLE) -> still drifting.
    # w2 has a DRIFTED assessment, but outside the period -> not drifting.
    assert content.drift.drifting == ["w1"]


def test_digest_is_json_serializable(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)
    content = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)

    payload = content.model_dump(mode="json")
    assert payload["drift"]["drifting"] == ["w1"]
    assert payload["spend"]["total_cost_usd"] == 25.0
    assert payload["approvals"]["total"] == 2
    assert payload["roi"]["rows"]


def test_digest_excludes_other_tenants(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)
    rig.cost.record(
        CostEvent(
            event_id="beta-1",
            tenant_id="beta",
            team_id="team-b",
            workload_id="w9",
            cost_type=CostType.LLM,
            cost_usd=999.0,
            completed=True,
            occurred_at=_NOW,
        )
    )
    rig.drift.add_quarantine(
        QuarantineRecord(
            quarantine_id="q-beta",
            workload="w9",
            reason="drift",
            timestamp=_NOW,
            tenant_id="beta",
        ),
        ctx=context_for_run("beta"),
    )

    content = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)

    assert content.spend.total_cost_usd == 25.0
    assert content.drift.quarantined == 1


def test_period_bounds_for_day_and_month(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)

    day = rig.service.build("default", kind=CostPeriodKind.DAY, at=_NOW)
    assert day.period_start == date(2026, 3, 11)
    assert day.period_end == date(2026, 3, 11)
    assert day.period_key == "2026-03-11"

    month = rig.service.build("default", kind=CostPeriodKind.MONTH, at=_NOW)
    assert month.period_start == date(2026, 3, 1)
    assert month.period_end == date(2026, 3, 31)
    assert month.period_key == "2026-03"


def test_render_markdown_has_all_sections(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)
    content = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)

    markdown = rig.service.render_markdown(content)

    assert "## Spend" in markdown
    assert "## ROI" in markdown
    assert "## Drift" in markdown
    assert "## Approvals" in markdown
    assert "w1" in markdown
    assert "alice" not in markdown and "bob" in markdown


def test_render_markdown_handles_empty_data(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    content = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)

    markdown = rig.service.render_markdown(content)

    assert "none" in markdown
    assert "n/a" in markdown


def test_generate_persists_run_and_audits(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)
    audit = InMemoryAuditLog(clock=rig.clock)
    rig.service.bind_audit(audit)

    content, report = rig.service.generate("default", kind=CostPeriodKind.WEEK, at=_NOW)

    assert report.kind is ReportKind.DIGEST
    assert report.tenant_id == "default"
    assert report.period_key == content.period_key
    assert report.generated_at == content.generated_at

    stored = rig.reporting.list_reports(tenant_id="default", ctx=context_for_run("default"))
    assert [item.report_id for item in stored] == [report.report_id]

    entries = audit.records(ctx=context_for_run("default"))
    assert any(entry.action == "report.digest.generated" for entry in entries)


def test_generate_is_idempotent_per_period(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)

    first, first_report = rig.service.generate("default", kind=CostPeriodKind.WEEK, at=_NOW)
    second, second_report = rig.service.generate("default", kind=CostPeriodKind.WEEK, at=_NOW)

    assert first_report.report_id == second_report.report_id
    assert first.period_key == second.period_key
    stored = rig.reporting.list_reports(tenant_id="default", ctx=context_for_run("default"))
    assert len(stored) == 1


def test_build_accepts_string_and_context(make_manifest: Callable[..., AgentWorkload]) -> None:
    rig = _rig(make_manifest)
    _seed_default(rig)

    from_string = rig.service.build("default", kind=CostPeriodKind.WEEK, at=_NOW)
    from_context = rig.service.build(
        context_for_run("default"), kind=CostPeriodKind.WEEK, at=_NOW
    )

    assert from_string == from_context
    assert isinstance(from_string, DigestContent)


def test_approval_record_helpers_are_exported() -> None:
    record = ApprovalRecord(
        approval_id="ap-1",
        run_id="r1",
        workload="w1",
        rule="rule",
        reason="escalated",
        requested_at=_NOW,
    )
    assert record.status is ApprovalStatus.PENDING
