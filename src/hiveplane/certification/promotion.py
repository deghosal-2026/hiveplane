"""The promotion gate: staging -> production on a certified artifact (M32-03..05).

Promotion is a request, not a mutation: the gate computes the target manifest's
artifact binding, looks for a valid, unexpired ``certified`` production
attestation bound to that exact hash, and either admits or refuses — naming the
exact changed binding(s). An uncertified workload can never be promoted under any
trust level. Every attempt is recorded and audited.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.certification.binding import ArtifactBinding, changed_bindings, compute_binding
from hiveplane.certification.models import (
    Attestation,
    CertificationRecord,
    CertificationStatus,
    Severity,
    TargetContext,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.persistence.audit import AuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class _Certifier(Protocol):
    def certify(
        self,
        workload: str,
        *,
        target_context: TargetContext,
        corpus_ref: str | None = ...,
        model_identity: str | None = ...,
    ) -> CertificationRecord: ...


class PromotionStore(Protocol):
    """Storage interface for promotion records."""

    def save_record(
        self, record: PromotionRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_record(
        self, promotion_id: str, *, ctx: TenantContext = ...
    ) -> PromotionRecord | None: ...

    def list_records(self, *, ctx: TenantContext = ...) -> list[PromotionRecord]: ...

    def clear(self) -> None: ...


def _critical_note(certification: CertificationRecord) -> str | None:
    """Return a promotion refusal note when re-certification shows critical regressions."""
    report = certification.regression_report
    if report is None or report.severity is not Severity.CRITICAL:
        return None
    ids = report.machine_report.get("critical_regressions", [])
    return f"critical regression on {ids}"


class PromotionRecord(BaseModel):
    """The recorded outcome of one promotion request."""

    model_config = ConfigDict(extra="forbid")

    promotion_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    workload: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    artifact_hash: str | None = None
    from_context: TargetContext
    to_context: TargetContext
    certification_id: str | None = None
    status: str
    refusal_reason: str | None = None
    changed_bindings: list[str] = Field(default_factory=list)
    operator: str = Field(min_length=1)
    timestamp: AwareDatetime

    @property
    def promoted(self) -> bool:
        """Whether the promotion was admitted."""
        return self.status == "promoted"


class PromotionGate:
    """Gates staging -> production on a valid certification for the artifact hash."""

    def __init__(
        self,
        registry: RegistryService,
        *,
        coordinator: _Certifier | None = None,
        store: PromotionStore | None = None,
        audit: AuditLog | None = None,
        policy_version_lookup: Callable[[str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._registry = registry
        self._coordinator = coordinator
        self._store = store
        self._audit = audit
        self._policy_version_lookup = policy_version_lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"promo-{uuid.uuid4().hex[:12]}")

    def promote(
        self,
        workload: str,
        version: int,
        *,
        to_context: TargetContext = TargetContext.PRODUCTION,
        operator: str,
        reason_note: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PromotionRecord:
        """Request promotion of a manifest version, admitting or refusing."""
        record = self._registry.get(workload)
        target = self._registry.get_version(workload, version)
        binding = compute_binding(target.manifest, policy_version=self._policy_version(workload))
        attestations = self._registry.list_attestations(workload)

        match, reason = self._find_valid_attestation(
            workload, version, binding.artifact_hash, to_context, record.certification_status
        )
        if match is None:
            if reason_note:
                reason = f"{reason}; {reason_note}"
            refusal_bindings = changed_bindings(self._latest_binding(attestations), binding)
            refusal = self._refuse(
                workload,
                version,
                binding.artifact_hash,
                to_context,
                reason,
                refusal_bindings,
                operator,
                ctx,
            )
            return self._record(refusal, ctx, action="promotion.refused")

        promotion = PromotionRecord(
            promotion_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            workload=workload,
            manifest_version=version,
            artifact_hash=binding.artifact_hash,
            from_context=TargetContext.STAGING,
            to_context=to_context,
            certification_id=match.attestation_id,
            status="promoted",
            operator=operator,
            timestamp=self._clock(),
        )
        return self._record(promotion, ctx, action="promotion.admitted")

    def recertify_and_promote(
        self,
        workload: str,
        *,
        corpus_ref: str | None = None,
        model_identity: str | None = None,
        to_context: TargetContext = TargetContext.PRODUCTION,
        operator: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> tuple[CertificationRecord, PromotionRecord]:
        """Run the benchmark for the current artifact, then attempt promotion (M32-05)."""
        if self._coordinator is None:
            raise ValueError("re-certification requires a certification coordinator")
        coordinator = self._coordinator
        record = self._registry.get(workload)
        if record.certification_status is CertificationStatus.UNCERTIFIED:
            coordinator.certify(
                workload,
                target_context=TargetContext.STAGING,
                corpus_ref=corpus_ref,
                model_identity=model_identity,
            )
        certification = coordinator.certify(
            workload,
            target_context=to_context,
            corpus_ref=corpus_ref,
            model_identity=model_identity,
        )
        current = self._registry.get(workload)
        promotion = self.promote(
            workload,
            current.current_version,
            to_context=to_context,
            operator=operator,
            reason_note=_critical_note(certification),
            ctx=ctx,
        )
        return certification, promotion

    def list_promotions(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PromotionRecord]:
        """Return recorded promotion attempts within the acting tenant."""
        if self._store is None:
            return []
        return self._store.list_records(ctx=ctx)

    def get(
        self, promotion_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PromotionRecord:
        """Return one recorded promotion attempt, or raise KeyError."""
        if self._store is None:
            raise KeyError(promotion_id)
        record = self._store.get_record(promotion_id, ctx=ctx)
        if record is None:
            raise KeyError(promotion_id)
        return record

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _policy_version(self, workload: str) -> str | None:
        if self._policy_version_lookup is None:
            return None
        return self._policy_version_lookup(workload)

    def _find_valid_attestation(
        self,
        workload: str,
        version: int,
        artifact_hash: str,
        to_context: TargetContext,
        status: CertificationStatus,
    ) -> tuple[Attestation | None, str]:
        record = self._registry.get(workload)
        if version != record.current_version:
            return None, (
                f"manifest version {version} is not the current version "
                f"{record.current_version}; publish it before promoting"
            )
        if status is CertificationStatus.QUARANTINED:
            return None, "workload is quarantined"
        if status is not CertificationStatus.CERTIFIED:
            return None, f"workload is {status.value}, not certified"
        if record.needs_re_certification:
            return None, "workload requires re-certification"
        decision = self._registry.check_admission(workload, AdmissionContext.PRODUCTION)
        if not decision.admitted:
            return None, decision.reason or "production admission refused"
        for attestation in reversed(self._registry.list_attestations(workload)):
            if (
                attestation.status is CertificationStatus.CERTIFIED
                and attestation.target_context is to_context
                and attestation.artifact_hash == artifact_hash
            ):
                return attestation, "admitted"
        return None, f"no valid {to_context.value} certification for the current artifact"

    @staticmethod
    def _latest_binding(attestations: list[Attestation]) -> ArtifactBinding | None:
        for attestation in reversed(attestations):
            if attestation.binding is not None:
                return attestation.binding
        return None

    def _refuse(
        self,
        workload: str,
        version: int,
        artifact_hash: str | None,
        to_context: TargetContext,
        reason: str,
        changed: list[str],
        operator: str,
        ctx: TenantContext,
    ) -> PromotionRecord:
        return PromotionRecord(
            promotion_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            workload=workload,
            manifest_version=version,
            artifact_hash=artifact_hash,
            from_context=TargetContext.STAGING,
            to_context=to_context,
            status="refused",
            refusal_reason=reason,
            changed_bindings=changed,
            operator=operator,
            timestamp=self._clock(),
        )

    def _record(
        self, promotion: PromotionRecord, ctx: TenantContext, *, action: str
    ) -> PromotionRecord:
        if self._store is not None:
            self._store.save_record(promotion, ctx=ctx)
        if self._audit is not None:
            detail = (
                f"{action} workload={promotion.workload} "
                f"version={promotion.manifest_version} hash={promotion.artifact_hash} "
                f"reason={promotion.refusal_reason or 'ok'}"
            )
            self._audit.append(
                promotion.operator, action, promotion.workload, detail=detail, ctx=ctx
            )
        return promotion
