"""Weekly fleet digest: spend, ROI, drift, and approvals (M57-01)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from hiveplane.cost.depth import RoiRow
from hiveplane.cost.service import CostService, period_key, period_start
from hiveplane.drift.models import DriftVerdict, QuarantineStatus
from hiveplane.drift.store import DriftStore
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.health.analytics import approval_analytics
from hiveplane.persistence.audit import AuditLog
from hiveplane.policy.approvals import ApprovalService
from hiveplane.registry.service import RegistryService
from hiveplane.reporting.models import DigestContent, DriftDigest, ReportKind, ReportRun
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy import TenantContext
from hiveplane.tenancy.context import context_for_run


class DigestService:
    """Builds, renders, and persists the periodic fleet digest."""

    def __init__(
        self,
        reporting_store: ReportingStore,
        cost_service: CostService,
        drift_store: DriftStore,
        approval_service: ApprovalService,
        registry_service: RegistryService,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._cost_service = cost_service
        self._drift_store = drift_store
        self._approval_service = approval_service
        self._registry_service = registry_service
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def build(
        self,
        tenant: str | TenantContext,
        *,
        kind: CostPeriodKind = CostPeriodKind.WEEK,
        at: datetime | None = None,
    ) -> DigestContent:
        """Compute the digest for one tenant and period."""
        ctx, tenant_id = _resolve(tenant)
        now = self._clock()
        when = at or now
        start, end = _period_bounds(kind, when)
        return DigestContent(
            tenant_id=tenant_id,
            period_key=period_key(kind, when),
            period_start=start,
            period_end=end,
            generated_at=now,
            spend=self._cost_service.showback(
                tenant_id, kind, at=when, group_by="team", ctx=ctx
            ),
            roi=self._cost_service.roi(tenant_id, kind, at=when, ctx=ctx),
            drift=self._drift_digest(ctx, start, end),
            approvals=approval_analytics(self._approval_service.list(ctx=ctx)),
        )

    def generate(
        self,
        tenant: str | TenantContext,
        *,
        kind: CostPeriodKind = CostPeriodKind.WEEK,
        at: datetime | None = None,
        actor: str = "system",
    ) -> tuple[DigestContent, ReportRun]:
        """Build a digest, persist a report run, and audit the generation."""
        ctx, tenant_id = _resolve(tenant)
        content = self.build(ctx, kind=kind, at=at)
        report = ReportRun(
            report_id=_report_id(tenant_id, content.period_key),
            tenant_id=tenant_id,
            kind=ReportKind.DIGEST,
            period_key=content.period_key,
            output_ref=f"reports/digest/{tenant_id}/{content.period_key}",
            generated_at=content.generated_at,
        )
        self._reporting_store.save_report(report, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "report.digest.generated",
                report.report_id,
                detail=f"tenant={tenant_id} period={content.period_key}",
                ctx=ctx,
            )
        return content, report

    def render_markdown(self, content: DigestContent) -> str:
        """Render a digest as a four-section markdown document."""
        latency = content.approvals.mean_latency_seconds
        lines = [
            f"# Fleet Digest — {content.tenant_id} ({content.period_key})",
            "",
            f"_Period: {content.period_start.isoformat()} → {content.period_end.isoformat()}._",
            "",
            "## Spend",
            f"- Total spend: ${content.spend.total_cost_usd:.2f}",
            f"- Completed tasks: {content.spend.total_completed_tasks}",
            f"- Cost per completed task: ${content.spend.fleet_cpct:.2f}",
            f"- Cache savings: ${content.spend.cache_savings_usd:.2f}",
            f"- Unattributed events: {content.spend.unattributed}",
            "",
            "## ROI",
            f"- Fleet ROI: {content.roi.fleet_roi:.2f}",
        ]
        ranked = sorted(content.roi.rows, key=lambda row: row.roi, reverse=True)
        lines.append(f"- Top workloads: {_format_rows(ranked[:3])}")
        lines.append(f"- Bottom workloads: {_format_rows(ranked[-3:])}")
        lines.extend(
            [
                "",
                "## Drift",
                f"- Quarantined this period: {content.drift.quarantined}",
                f"- Reinstated this period: {content.drift.reinstated}",
                f"- Active quarantines: {content.drift.active}",
                f"- Drifting workloads: {', '.join(content.drift.drifting) or 'none'}",
                "",
                "## Approvals",
                (
                    f"- Total: {content.approvals.total} "
                    f"(pending {content.approvals.pending}, decided {content.approvals.decided})"
                ),
                (
                    f"- Approved: {content.approvals.approved}, "
                    f"denied: {content.approvals.denied}"
                ),
                (
                    "- Mean latency: "
                    + ("n/a" if latency is None else f"{latency:.1f}s")
                ),
                f"- Bottleneck: {content.approvals.bottleneck or 'none'}",
            ]
        )
        return "\n".join(lines)

    def _drift_digest(self, ctx: TenantContext, start: date, end: date) -> DriftDigest:
        records = self._drift_store.list_quarantines(ctx=ctx)
        quarantined = sum(1 for record in records if start <= record.timestamp.date() <= end)
        reinstated = sum(
            1
            for record in records
            if record.reinstated_at is not None
            and start <= record.reinstated_at.date() <= end
        )
        active = sum(1 for record in records if record.status is QuarantineStatus.ACTIVE)
        drifting: list[str] = []
        for workload in self._registry_service.list_workloads(ctx=ctx):
            assessments = self._drift_store.list_assessments(workload.name, ctx=ctx)
            if any(
                assessment.verdict is DriftVerdict.DRIFTED
                and start <= assessment.timestamp.date() <= end
                for assessment in assessments
            ):
                drifting.append(workload.name)
        return DriftDigest(
            quarantined=quarantined,
            reinstated=reinstated,
            active=active,
            drifting=sorted(drifting),
        )


def _resolve(tenant: str | TenantContext) -> tuple[TenantContext, str]:
    if isinstance(tenant, TenantContext):
        return tenant, tenant.tenant_id
    return context_for_run(tenant), tenant


def _period_bounds(kind: CostPeriodKind, when: datetime) -> tuple[date, date]:
    start = period_start(kind, when)
    if kind is CostPeriodKind.DAY:
        return start, start
    if kind is CostPeriodKind.WEEK:
        return start, start + timedelta(days=6)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def _report_id(tenant_id: str, key: str) -> str:
    token = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:12]
    return f"digest-{key.lower()}-{token}"


def _format_rows(rows: list[RoiRow]) -> str:
    if not rows:
        return "none"
    return "; ".join(
        f"{row.workload_id} (roi={row.roi:.2f}, spend=${row.spend_usd:.2f})" for row in rows
    )
