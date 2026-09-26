"""Auto-quarantine action: revoke admission, cancel in-flight work (M34-03)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from hiveplane.certification.models import CertificationStatus, Severity
from hiveplane.drift.models import (
    DriftAssessment,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.drift.notify import DriftNotifier
from hiveplane.drift.store import DriftStore
from hiveplane.persistence.audit import AuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class RunCanceller(Protocol):
    """Cancels or pauses a workload's in-flight runs on quarantine (M34-03)."""

    def cancel_in_flight(self, workload: str) -> list[str]: ...


class QuarantineService:
    """Marks workloads quarantined, persists history, notifies, and audits."""

    def __init__(
        self,
        registry: RegistryService,
        store: DriftStore,
        *,
        audit: AuditLog | None = None,
        notifier: DriftNotifier | None = None,
        canceller: RunCanceller | None = None,
        cancel_in_flight: bool = True,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._audit = audit
        self._notifier = notifier
        self._canceller = canceller
        self._cancel_in_flight = cancel_in_flight
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"quar-{uuid.uuid4().hex[:12]}")

    def quarantine(
        self,
        workload: str,
        *,
        reason: str,
        assessment: DriftAssessment | None = None,
        regression_diff: dict[str, JsonValue] | None = None,
        severity: Severity | None = None,
        actor: str = "drift-detector",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> QuarantineRecord:
        """Quarantine a workload; idempotent while an active quarantine exists."""
        record = self._registry.get(workload)
        if record.certification_status is CertificationStatus.QUARANTINED:
            existing = self._store.active_quarantine(workload, ctx=ctx)
            if existing is not None:
                return existing
        self._registry.quarantine(workload)
        cancelled = list(self._cancel(workload))
        resolved_severity = severity or self._severity(assessment)
        quarantine = QuarantineRecord(
            quarantine_id=self._id_factory(),
            workload=workload,
            status=QuarantineStatus.ACTIVE,
            severity=resolved_severity,
            reason=reason,
            actor=actor,
            baseline_attestation_id=(
                assessment.baseline_attestation_id if assessment else None
            ),
            assessment=assessment,
            evidence=dict(assessment.evidence) if assessment else {},
            regression_diff=regression_diff,
            cancelled_runs=cancelled,
            tenant_id=record.tenant_id,
            timestamp=self._clock(),
        )
        if self._notifier is not None:
            notified = self._notifier.notify_quarantine(quarantine, owner=record.owner)
            quarantine = quarantine.model_copy(update={"notified": notified})
        self._store.add_quarantine(quarantine, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "workload.quarantined",
                workload,
                detail=(
                    f"severity={resolved_severity.value} reason={reason} "
                    f"cancelled_runs={len(cancelled)}"
                ),
                ctx=ctx,
            )
        return quarantine

    def get(
        self, quarantine_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> QuarantineRecord | None:
        """Return a quarantine record by id, or ``None``."""
        return self._store.get_quarantine(quarantine_id, ctx=ctx)

    def list(
        self,
        *,
        workload: str | None = None,
        status: QuarantineStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[QuarantineRecord]:
        """List quarantine history, optionally filtered."""
        return self._store.list_quarantines(workload=workload, status=status, ctx=ctx)

    def _cancel(self, workload: str) -> Sequence[str]:
        if not self._cancel_in_flight or self._canceller is None:
            return []
        return self._canceller.cancel_in_flight(workload)

    @staticmethod
    def _severity(assessment: DriftAssessment | None) -> Severity:
        if assessment is not None and (
            assessment.critical_failures > 0 or assessment.strong_signal
        ):
            return Severity.CRITICAL
        return Severity.WARNING
