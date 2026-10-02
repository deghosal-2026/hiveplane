"""Digest scheduling and per-team routing (M57-02)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.delivery.models import (
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryEnvelope,
    DeliveryEventType,
)
from hiveplane.delivery.service import DeliveryService
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.persistence.audit import AuditLog
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.models import ReportSchedule
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    SYSTEM_CONTEXT,
    TenantContext,
    context_for_run,
)
from hiveplane.triggers.cron import CronExpression


class DigestScheduler:
    """Schedules periodic digests and routes each to its team's destinations."""

    def __init__(
        self,
        reporting_store: ReportingStore,
        digest_service: DigestService,
        delivery_service: DeliveryService,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._digest_service = digest_service
        self._delivery_service = delivery_service
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def schedule(
        self,
        tenant_id: str,
        *,
        cron: str,
        channels: list[DeliveryChannel],
        team_id: str | None = None,
        period_kind: CostPeriodKind = CostPeriodKind.WEEK,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportSchedule:
        """Persist a digest schedule with its next fire time."""
        now = self._clock()
        expression = CronExpression.parse(cron)
        schedule = ReportSchedule(
            schedule_id=_schedule_id(tenant_id, team_id, cron),
            tenant_id=tenant_id,
            team_id=team_id,
            cron=cron,
            period_kind=period_kind,
            channels=channels,
            active=True,
            next_run=expression.next_after(now),
            created_at=now,
        )
        self._reporting_store.save_schedule(schedule, ctx=ctx)
        return schedule

    def due(
        self, *, at: datetime | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ReportSchedule]:
        """Return the active schedules whose next run has matured."""
        now = at or self._clock()
        return [
            schedule
            for schedule in self._reporting_store.list_schedules(ctx=ctx)
            if schedule.active
            and schedule.next_run is not None
            and schedule.next_run <= now
        ]

    def run_due(
        self, *, at: datetime | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]:
        """Generate, deliver, and advance every matured schedule."""
        now = at or self._clock()
        attempts: list[DeliveryAttempt] = []
        for schedule in self.due(at=now, ctx=ctx):
            schedule_ctx = context_for_run(schedule.tenant_id)
            if schedule.next_run is None:
                continue
            # Claim the fire before delivering so a concurrent ticker or the
            # manual endpoint cannot deliver the same matured schedule twice.
            next_run = CronExpression.parse(schedule.cron).next_after(now)
            claimed = self._reporting_store.claim_schedule(
                schedule.schedule_id,
                expected_next_run=schedule.next_run,
                new_next_run=next_run,
                active=next_run is not None,
                ctx=schedule_ctx,
            )
            if not claimed:
                continue
            preference = self._delivery_service.preference(
                schedule.team_id, schedule.tenant_id, ctx=schedule_ctx
            )
            if preference is not None and preference.tenant_id == schedule.tenant_id:
                destinations = list(preference.destinations)
            else:
                destinations = []
            if destinations:
                content, _ = self._digest_service.generate(
                    schedule.tenant_id, kind=schedule.period_kind, at=now
                )
                envelope = DeliveryEnvelope(
                    event_type=DeliveryEventType.COMPLETED,
                    tenant_id=schedule.tenant_id,
                    team_id=schedule.team_id,
                    summary=self._digest_service.render_markdown(content),
                )
                attempts.extend(
                    self._delivery_service.deliver(
                        envelope, destinations, ctx=schedule_ctx
                    )
                )
                self._audit_schedule(schedule, "report.digest.delivered", ctx)
            else:
                self._audit_schedule(schedule, "report.digest.skipped", ctx)
        return attempts

    def run_all_due(
        self, *, at: datetime | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]:
        """Run every matured schedule; a system context sweeps all tenants.

        Under :data:`SYSTEM_CONTEXT` the scheduler fans out to each tenant that
        has schedules and runs only that tenant's matured schedules; under a
        tenant context it behaves exactly like :meth:`run_due`.
        """
        if not ctx.is_system:
            return self.run_due(at=at, ctx=ctx)
        now = at or self._clock()
        schedules = self._reporting_store.list_schedules(ctx=SYSTEM_CONTEXT)
        attempts: list[DeliveryAttempt] = []
        for tenant_id in sorted({schedule.tenant_id for schedule in schedules}):
            attempts.extend(self.run_due(at=now, ctx=context_for_run(tenant_id)))
        return attempts

    def _audit_schedule(
        self,
        schedule: ReportSchedule,
        action: str,
        ctx: TenantContext,
    ) -> None:
        if self._audit is None:
            return
        self._audit.append(
            "system",
            action,
            schedule.schedule_id,
            detail=(
                f"tenant={schedule.tenant_id} team={schedule.team_id} "
                f"period_kind={schedule.period_kind.value}"
            ),
            ctx=ctx,
        )


def _schedule_id(tenant_id: str, team_id: str | None, cron: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}|{team_id or ''}|{cron}".encode()).hexdigest()
    return f"sched-{digest[:20]}"
