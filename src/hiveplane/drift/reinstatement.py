"""Reinstatement flow: fresh certification, resume admission, audit (M34-06)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import CertificationStatus, TargetContext
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.run import AdmissionContext
from hiveplane.drift.errors import QuarantineNotFoundError, ReinstatementRefusedError
from hiveplane.drift.models import QuarantineRecord, QuarantineStatus
from hiveplane.drift.store import DriftStore
from hiveplane.persistence.audit import AuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class ReinstatementService:
    """Restores a quarantined workload only after a fresh passing certification."""

    def __init__(
        self,
        registry: RegistryService,
        store: DriftStore,
        coordinator: CertificationCoordinator,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._coordinator = coordinator
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"rein-{uuid.uuid4().hex[:12]}")

    def reinstate(
        self,
        workload: str,
        *,
        operator: str,
        corpus_ref: str | None = None,
        model_identity: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> QuarantineRecord:
        """Re-certify a quarantined workload and resume admission.

        The quarantine is only lifted when a *fresh* certification reaches
        ``certified`` and production admission is granted again. Any failure
        leaves the quarantine active and raises.
        """
        active = self._store.active_quarantine(workload, ctx=ctx)
        if active is None:
            raise QuarantineNotFoundError(workload)
        record = self._registry.get(workload)
        if record.certification_status is not CertificationStatus.QUARANTINED:
            raise ReinstatementRefusedError(
                workload,
                f"workload is {record.certification_status.value}, not quarantined",
            )
        self._coordinator.certify(
            workload,
            target_context=TargetContext.STAGING,
            corpus_ref=corpus_ref,
            model_identity=model_identity,
        )
        production = self._coordinator.certify(
            workload,
            target_context=TargetContext.PRODUCTION,
            corpus_ref=corpus_ref,
            model_identity=model_identity,
        )
        if production.certification.status is not CertificationStatus.CERTIFIED:
            raise ReinstatementRefusedError(
                workload,
                "fresh certification did not reach 'certified' "
                f"(got {production.certification.status.value!r})",
            )
        decision = self._registry.check_admission(workload, AdmissionContext.PRODUCTION)
        if not decision.admitted:
            raise ReinstatementRefusedError(
                workload, decision.reason or "production admission still refused"
            )
        reinstated = active.model_copy(
            update={
                "status": QuarantineStatus.REINSTATED,
                "reinstated_at": self._clock(),
                "reinstated_by": operator,
            }
        )
        self._store.save_quarantine(reinstated, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                operator,
                "workload.reinstated",
                workload,
                detail=f"fresh certification {production.record_id} "
                f"attestation={production.attestation.attestation_id}",
                ctx=ctx,
            )
        return reinstated
