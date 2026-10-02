"""Drift monitor: baseline resolution, trend tracking, auto-quarantine (M34-02)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import (
    CertificationRecord,
    CertificationStatus,
    CheckStatus,
    EvalSummary,
    TargetContext,
)
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.drift.detector import DriftDetector
from hiveplane.drift.errors import DriftNotConfiguredError
from hiveplane.drift.models import DriftAssessment
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.store import DriftStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class DriftMonitor:
    """Evaluates performance against the certified baseline and quarantines drift."""

    def __init__(
        self,
        detector: DriftDetector,
        store: DriftStore,
        coordinator: CertificationCoordinator,
        *,
        quarantine_service: QuarantineService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._detector = detector
        self._store = store
        self._coordinator = coordinator
        self._quarantine_service = quarantine_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"assess-{uuid.uuid4().hex[:12]}")

    def _baseline_record(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CertificationRecord | None:
        """Return the workload's most recent certified record, if any."""
        certified = [
            record
            for record in self._coordinator.list(workload=workload, ctx=ctx)
            if record.certification.status is CertificationStatus.CERTIFIED
        ]
        if not certified:
            return None
        certified.sort(key=lambda record: (record.attestation.timestamp, record.record_id))
        return certified[-1]

    def baseline(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> tuple[EvalSummary, str | None]:
        """Return the latest certified record's eval summary and attestation id."""
        latest = self._baseline_record(workload, ctx=ctx)
        if latest is None:
            raise DriftNotConfiguredError(
                workload, "no certified baseline record to compare against"
            )
        return latest.attestation.eval_summary, latest.attestation.attestation_id

    def evaluate(
        self,
        workload: str,
        current: EvalSummary,
        *,
        baseline_attestation_id: str | None = None,
        actor: str = "drift-detector",
        ctx: TenantContext = DEFAULT_CONTEXT,
        baseline_task_ids: frozenset[str] | None = None,
        baseline_failed_task_ids: frozenset[str] | None = None,
        current_failed_task_ids: frozenset[str] | None = None,
    ) -> DriftAssessment:
        """Assess ``current`` against the certified baseline and act on drift."""
        baseline, resolved_id = self.baseline(workload, ctx=ctx)
        streak = self._store.consecutive_failures(workload, ctx=ctx)
        assessment = self._detector.assess(
            workload=workload,
            baseline=baseline,
            current=current,
            baseline_attestation_id=baseline_attestation_id or resolved_id,
            consecutive_failures=streak + 1,
            tenant_id=ctx.tenant_id or "default",
            baseline_task_ids=baseline_task_ids,
            baseline_failed_task_ids=baseline_failed_task_ids,
            current_failed_task_ids=current_failed_task_ids,
        )
        self._store.add_assessment(assessment, ctx=ctx)
        if assessment.should_quarantine and self._quarantine_service is not None:
            self._quarantine_service.quarantine(
                workload,
                reason=assessment.reason,
                assessment=assessment,
                actor=actor,
                ctx=ctx,
            )
        return assessment

    def probe_and_evaluate(
        self,
        workload: str,
        *,
        target_context: TargetContext = TargetContext.STAGING,
        corpus_ref: str | None = None,
        model_identity: str | None = None,
        actor: str = "drift-detector",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> DriftAssessment:
        """Run a fresh benchmark for the workload, then evaluate it for drift."""
        result = self._coordinator.benchmark(
            workload,
            target_context=target_context,
            corpus_ref=corpus_ref,
            model_identity=model_identity,
            ctx=ctx,
        )
        baseline = self._baseline_record(workload, ctx=ctx)
        baseline_ids = (
            frozenset(task.task_id for task in baseline.benchmark_result.tasks)
            if baseline is not None
            else None
        )
        baseline_failed = (
            frozenset(
                task.task_id
                for task in baseline.benchmark_result.tasks
                if task.status is CheckStatus.FAIL
            )
            if baseline is not None
            else None
        )
        current_failed = frozenset(
            task.task_id for task in result.tasks if task.status is CheckStatus.FAIL
        )
        return self.evaluate(
            workload,
            result.to_eval_summary(),
            actor=actor,
            ctx=ctx,
            baseline_task_ids=baseline_ids,
            baseline_failed_task_ids=baseline_failed,
            current_failed_task_ids=current_failed,
        )
