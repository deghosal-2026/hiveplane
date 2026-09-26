"""Trigger evaluation and idempotent run submission (M27-07, D23).

The engine is the handoff from an accepted event to the execution API: it applies
dedup/cooldown/rate limits, renders the typed task template, resolves the
admission rule, submits through :class:`RunService`, and records the event and
its run linkage. Admission never bypasses certification — a production trigger
still requires a valid certification, and a refusal is recorded as
``blocked_cert`` or ``blocked_admission`` with a reason. A delivery that fails
after the submit attempt is parked in the DLQ with its payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.run import AdmissionContext, Run, TriggerOrigin
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.fleet.triggers import (
    TriggerDlqEntry,
    TriggerEvent,
    TriggerOutcome,
    TriggerRun,
    TriggerRunStatus,
    TriggerSource,
)
from hiveplane.persistence.audit import AuditLog
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.triggers.limiter import LimiterDecision, TriggerLimiter
from hiveplane.triggers.schema import AdmissionRule, TriggerSpec
from hiveplane.triggers.store import TriggerStore
from hiveplane.triggers.templating import TemplateError, render_task, render_template


class TriggerDisabledError(Exception):
    """Raised when an event is delivered to a disabled trigger."""

    def __init__(self, trigger_id: str) -> None:
        super().__init__(f"trigger {trigger_id!r} is disabled")
        self.trigger_id = trigger_id


class RunSubmitter(Protocol):
    """The execution-API surface the engine submits through."""

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue],
        trigger_origin: TriggerOrigin | None,
        ctx: TenantContext,
    ) -> Run: ...


class TriggerDecision(BaseModel):
    """The recorded outcome of evaluating one trigger event."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    event_id: str
    outcome: TriggerOutcome = TriggerOutcome.ACCEPTED
    run_id: str | None = None
    status: TriggerRunStatus | None = None
    reason: str | None = Field(default=None, max_length=2000)
    dedup_key: str | None = None


class TriggerEngine:
    """Evaluates trigger events and hands admitted ones to the execution API."""

    def __init__(
        self,
        store: TriggerStore,
        limiter: TriggerLimiter,
        submitter: RunSubmitter,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._store = store
        self._limiter = limiter
        self._submitter = submitter
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"ev-{uuid.uuid4().hex[:12]}")
        self._audit = audit

    def ingest(
        self,
        spec: TriggerSpec,
        payload: dict[str, JsonValue],
        *,
        event_id: str | None = None,
        source: TriggerSource | None = None,
        dedup_key: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> TriggerDecision:
        """Evaluate an event for ``spec`` and submit a run when admitted."""
        if not spec.enabled:
            raise TriggerDisabledError(spec.id)
        eid = event_id or self._id_factory()
        src = source or spec.source
        now = self._clock()

        existing = self._existing_run(spec.id, eid, ctx)
        if existing is not None:
            return TriggerDecision(
                trigger_id=spec.id,
                event_id=eid,
                outcome=TriggerOutcome.DEDUPLICATED,
                run_id=existing.run_id,
                status=existing.status,
                reason="duplicate event id",
            )

        key = dedup_key
        if key is None and spec.dedup is not None and spec.dedup.key is not None:
            try:
                key = str(render_template(spec.dedup.key, payload))
            except TemplateError as exc:
                return self._fail(spec, eid, src, payload, f"dedup key: {exc}", ctx)

        decision = self._limiter.check(spec, dedup_key=key, ctx=ctx)
        if decision is not LimiterDecision.ACCEPT:
            outcome = decision.outcome or TriggerOutcome.FAILED
            self._record_event(spec, eid, src, payload, key, outcome, now, ctx)
            return TriggerDecision(
                trigger_id=spec.id, event_id=eid, outcome=outcome, dedup_key=key
            )

        try:
            task = render_task(
                spec.task_template,
                payload,
                max_field_bytes=spec.max_field_bytes,
                max_total_bytes=spec.max_total_bytes,
            )
        except TemplateError as exc:
            return self._fail(spec, eid, src, payload, f"template: {exc}", ctx)

        if spec.admission_rule is AdmissionRule.DENY:
            self._record_event(spec, eid, src, payload, key, TriggerOutcome.ACCEPTED, now, ctx)
            self._record_run(
                spec,
                eid,
                None,
                TriggerRunStatus.BLOCKED_ADMISSION,
                "admission rule denies",
                now,
                ctx,
            )
            return TriggerDecision(
                trigger_id=spec.id,
                event_id=eid,
                status=TriggerRunStatus.BLOCKED_ADMISSION,
                reason="admission rule denies",
                dedup_key=key,
            )

        context = (
            AdmissionContext.STAGING
            if spec.admission_rule is AdmissionRule.STAGING_AUTO
            else AdmissionContext.PRODUCTION
        )
        try:
            run = self._submitter.submit(
                workload=spec.target.ref,
                caller=f"trigger:{spec.id}",
                context=context,
                task=task,
                trigger_origin=TriggerOrigin(
                    source=src.value, event_id=eid, timestamp=now
                ),
                ctx=ctx,
            )
        except RunAdmissionRefusedError as exc:
            status = (
                TriggerRunStatus.BLOCKED_CERT
                if _certification_failed(exc)
                else TriggerRunStatus.BLOCKED_ADMISSION
            )
            reason = exc.result.refused_reason or "admission refused"
            self._record_event(spec, eid, src, payload, key, TriggerOutcome.ACCEPTED, now, ctx)
            self._record_run(spec, eid, None, status, reason, now, ctx)
            return TriggerDecision(
                trigger_id=spec.id,
                event_id=eid,
                status=status,
                reason=reason,
                dedup_key=key,
            )
        except Exception as exc:
            return self._fail(spec, eid, src, payload, str(exc), ctx)

        self._record_event(spec, eid, src, payload, key, TriggerOutcome.ACCEPTED, now, ctx)
        self._record_run(spec, eid, run.id, TriggerRunStatus.SUBMITTED, None, now, ctx)
        self._audit_append(
            "trigger-engine", "trigger.run.submitted", spec.id, f"run {run.id}", ctx
        )
        return TriggerDecision(
            trigger_id=spec.id,
            event_id=eid,
            status=TriggerRunStatus.SUBMITTED,
            run_id=run.id,
            dedup_key=key,
        )

    def _existing_run(
        self, trigger_id: str, event_id: str, ctx: TenantContext
    ) -> TriggerRun | None:
        for run in self._store.list_runs(trigger_id, ctx=ctx):
            if run.event_id == event_id:
                return run
        return None

    def _record_event(
        self,
        spec: TriggerSpec,
        event_id: str,
        source: TriggerSource,
        payload: dict[str, JsonValue],
        dedup_key: str | None,
        outcome: TriggerOutcome,
        now: datetime,
        ctx: TenantContext,
    ) -> None:
        self._store.add_event(
            TriggerEvent(
                event_id=event_id,
                trigger_id=spec.id,
                tenant_id=ctx.tenant_id,
                source=source,
                payload=payload,
                received_at=now,
                dedup_key=dedup_key,
                outcome=outcome,
            ),
            ctx=ctx,
        )

    def _record_run(
        self,
        spec: TriggerSpec,
        event_id: str,
        run_id: str | None,
        status: TriggerRunStatus,
        reason: str | None,
        now: datetime,
        ctx: TenantContext,
    ) -> None:
        self._store.add_run(
            TriggerRun(
                trigger_id=spec.id,
                event_id=event_id,
                run_id=run_id,
                tenant_id=ctx.tenant_id,
                fired_at=now,
                status=status,
                reason=reason,
            ),
            ctx=ctx,
        )

    def _fail(
        self,
        spec: TriggerSpec,
        event_id: str,
        source: TriggerSource,
        payload: dict[str, JsonValue],
        reason: str,
        ctx: TenantContext,
    ) -> TriggerDecision:
        now = self._clock()
        self._record_event(spec, event_id, source, payload, None, TriggerOutcome.FAILED, now, ctx)
        self._store.add_dlq(
            TriggerDlqEntry(
                entry_id=f"dlq-{event_id}",
                trigger_id=spec.id,
                event_id=event_id,
                tenant_id=ctx.tenant_id,
                payload=payload,
                headers={},
                failure_reason=reason,
                attempts=1,
                created_at=now,
            ),
            ctx=ctx,
        )
        self._audit_append("trigger-engine", "trigger.run.failed", spec.id, reason, ctx)
        return TriggerDecision(
            trigger_id=spec.id,
            event_id=event_id,
            outcome=TriggerOutcome.FAILED,
            reason=reason,
        )

    def _audit_append(
        self, actor: str, action: str, subject: str, detail: str, ctx: TenantContext
    ) -> None:
        if self._audit is not None:
            self._audit.append(actor, action, subject, detail=detail, ctx=ctx)


def _certification_failed(exc: RunAdmissionRefusedError) -> bool:
    return any(
        check.step == "certification" and not check.passed for check in exc.result.checks
    )
