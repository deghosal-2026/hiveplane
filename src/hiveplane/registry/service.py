"""Registry service: desired state, versioning, and admission (M3-M4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, overload

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hiveplane import metrics
from hiveplane.certification.models import (
    Attestation,
    CertificationEvent,
    CertificationStatus,
    advance_status,
)
from hiveplane.certification.signing import verify_attestation
from hiveplane.core.decision import ActionClass
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.triggers import TriggerRule
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import (
    AdmissionRefusedError,
    AttestationAlreadyExistsError,
    AttestationNotFoundError,
    AttestationVerificationError,
    DestructiveToolRequiresApprovalError,
    ReCertificationRequiredError,
    ToolAlreadyExistsError,
    UnknownToolError,
    VersionNotFoundError,
    WorkloadAlreadyExistsError,
    WorkloadNotFoundError,
)
from hiveplane.registry.models import (
    AdmissionContext,
    AdmissionDecision,
    EnforcementSummary,
    ToolRecord,
    TriggerRecord,
    VersionDiff,
    VersionStatus,
    WorkloadRecord,
    WorkloadVersion,
)
from hiveplane.registry.store import RegistryStore

CERT_RELEVANT_FIELDS: tuple[str, ...] = (
    "spec.runtime",
    "spec.model",
    "spec.tools",
    "spec.certification.benchmark_corpus",
)

_SORT_KEYS: dict[str, Callable[[WorkloadRecord], Any]] = {
    "name": lambda record: record.name,
    "owner": lambda record: record.owner,
    "team": lambda record: record.team or "",
    "certification_status": lambda record: record.certification_status.value,
    "updated_at": lambda record: record.updated_at,
}


def changed_fields(old: AgentWorkload, new: AgentWorkload) -> list[str]:
    """Return the manifest fields that differ between two versions."""
    changes: list[str] = []
    old_spec = old.spec
    new_spec = new.spec

    if old_spec.runtime != new_spec.runtime:
        changes.append("spec.runtime")
    if old_spec.model != new_spec.model:
        changes.append("spec.model")
    if old_spec.tools != new_spec.tools:
        changes.append("spec.tools")
    old_corpus = old_spec.certification.benchmark_corpus if old_spec.certification else None
    new_corpus = new_spec.certification.benchmark_corpus if new_spec.certification else None
    if old_corpus != new_corpus:
        changes.append("spec.certification.benchmark_corpus")
    if old_spec.budget != new_spec.budget:
        changes.append("spec.budget")
    if old_spec.approvals != new_spec.approvals:
        changes.append("spec.approvals")
    if old_spec.triggers != new_spec.triggers:
        changes.append("spec.triggers")
    if old_spec.fan_out != new_spec.fan_out:
        changes.append("spec.fan_out")
    if old_spec.output_shaping != new_spec.output_shaping:
        changes.append("spec.output_shaping")
    if old_spec.health != new_spec.health:
        changes.append("spec.health")
    if old_spec.observability != new_spec.observability:
        changes.append("spec.observability")
    if old.metadata != new.metadata:
        changes.append("metadata")
    return changes


def requires_re_certification(changes: list[str]) -> bool:
    """Return True if any changed field is certification-relevant."""
    return any(field in CERT_RELEVANT_FIELDS for field in changes)


def _survival_after_transition(current: int, status: CertificationStatus) -> int:
    """Reset the production-run survival counter when a new survival cycle begins."""
    if status in (CertificationStatus.PROVISIONAL, CertificationStatus.QUARANTINED):
        return 0
    return current


class RegistryService:
    """Desired-state registry over a :class:`RegistryStore`."""

    def __init__(
        self,
        store: RegistryStore,
        *,
        clock: Callable[[], datetime] | None = None,
        attestation_public_key: Ed25519PublicKey | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._attestation_public_key = attestation_public_key

    def _now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------ #
    # Workload CRUD
    # ------------------------------------------------------------------ #
    def _build_record(
        self,
        manifest: AgentWorkload,
        *,
        version: int,
        created_at: datetime,
        needs_re_certification: bool = False,
    ) -> WorkloadRecord:
        return WorkloadRecord(
            name=manifest.name,
            manifest=manifest,
            current_version=version,
            certification_status=manifest.certification_status,
            owner=manifest.owner,
            team=manifest.team,
            runtime=manifest.spec.runtime.adapter,
            created_at=created_at,
            updated_at=created_at,
            needs_re_certification=needs_re_certification,
        )

    @overload
    def create(
        self, manifest: AgentWorkload, *, dry_run: Literal[False] = False
    ) -> WorkloadRecord: ...

    @overload
    def create(
        self, manifest: AgentWorkload, *, dry_run: Literal[True]
    ) -> EnforcementSummary: ...

    def create(
        self, manifest: AgentWorkload, *, dry_run: bool = False
    ) -> WorkloadRecord | EnforcementSummary:
        """Register a new workload, or report enforcement with ``dry_run``."""
        if dry_run:
            return self.enforcement_summary(manifest)
        if self._store.get_workload(manifest.name) is not None:
            raise WorkloadAlreadyExistsError(manifest.name)
        self._validate_tools(manifest)
        now = self._now()
        record = self._build_record(manifest, version=1, created_at=now)
        self._store.save_workload(record)
        self._store.add_version(
            WorkloadVersion(
                workload=manifest.name,
                version=1,
                manifest=manifest,
                status=VersionStatus.ACTIVE,
                created_at=now,
            )
        )
        self._sync_triggers(manifest)
        return record

    @overload
    def update(
        self,
        name: str,
        manifest: AgentWorkload,
        *,
        dry_run: Literal[False] = False,
    ) -> WorkloadRecord: ...

    @overload
    def update(
        self, name: str, manifest: AgentWorkload, *, dry_run: Literal[True]
    ) -> EnforcementSummary: ...

    def update(
        self, name: str, manifest: AgentWorkload, *, dry_run: bool = False
    ) -> WorkloadRecord | EnforcementSummary:
        """Register a new manifest version for an existing workload."""
        existing = self.get(name)
        if dry_run:
            return self.enforcement_summary(manifest)
        self._validate_tools(manifest)

        changes = changed_fields(existing.manifest, manifest)
        re_cert = requires_re_certification(changes)
        now = self._now()
        new_version = existing.current_version + 1

        for version in self._store.list_versions(name):
            if version.status is VersionStatus.ACTIVE:
                self._store.save_version(
                    version.model_copy(update={"status": VersionStatus.SUPERSEDED})
                )

        self._store.add_version(
            WorkloadVersion(
                workload=name,
                version=new_version,
                manifest=manifest,
                status=VersionStatus.ACTIVE,
                created_at=now,
                re_certification_required=re_cert,
                changed_fields=changes,
            )
        )
        record = self._build_record(
            manifest,
            version=new_version,
            created_at=existing.created_at,
            needs_re_certification=re_cert,
        ).model_copy(update={"updated_at": now})
        self._store.save_workload(record)
        self._sync_triggers(manifest)
        return record

    def get(self, name: str) -> WorkloadRecord:
        """Return the current record for a workload."""
        record = self._store.get_workload(name)
        if record is None:
            raise WorkloadNotFoundError(name)
        return record

    def list_workloads(
        self,
        *,
        owner: str | None = None,
        team: str | None = None,
        runtime: RuntimeAdapter | str | None = None,
        certification_status: CertificationStatus | str | None = None,
        sort: str = "name",
        descending: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkloadRecord]:
        """Return the fleet catalog, filtered, sorted, and paginated."""
        if sort not in _SORT_KEYS:
            raise ValueError(
                f"invalid sort field {sort!r}; expected one of {sorted(_SORT_KEYS)}"
            )
        runtime_filter = self._coerce_runtime(runtime)
        status_filter = self._coerce_status(certification_status)

        records = self._store.list_workloads()
        if owner is not None:
            records = [record for record in records if record.owner == owner]
        if team is not None:
            records = [record for record in records if record.team == team]
        if runtime_filter is not None:
            records = [record for record in records if record.runtime is runtime_filter]
        if status_filter is not None:
            records = [
                record for record in records if record.certification_status is status_filter
            ]

        records.sort(key=_SORT_KEYS[sort], reverse=descending)
        if offset:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]
        return records

    def delete(self, name: str) -> None:
        """Deregister a workload."""
        if not self._store.delete_workload(name):
            raise WorkloadNotFoundError(name)

    # ------------------------------------------------------------------ #
    # Versioning
    # ------------------------------------------------------------------ #
    def versions(self, name: str) -> list[WorkloadVersion]:
        """Return the append-only manifest version history."""
        self.get(name)
        return self._store.list_versions(name)

    def version_diff(self, name: str, from_version: int, to_version: int) -> VersionDiff:
        """Return the field-level diff between two manifest versions."""
        self.get(name)
        before = self._store.get_version(name, from_version)
        if before is None:
            raise VersionNotFoundError(name, from_version)
        after = self._store.get_version(name, to_version)
        if after is None:
            raise VersionNotFoundError(name, to_version)
        changes = changed_fields(before.manifest, after.manifest)
        return VersionDiff(
            workload=name,
            from_version=from_version,
            to_version=to_version,
            changed_fields=changes,
            re_certification_required=requires_re_certification(changes),
        )

    def promote(self, name: str, version: int) -> WorkloadRecord:
        """Promote a manifest version, blocking until re-certification passes."""
        record = self.get(name)
        target = self._store.get_version(name, version)
        if target is None:
            raise VersionNotFoundError(name, version)
        if target.re_certification_required and record.needs_re_certification:
            raise ReCertificationRequiredError(name, target.changed_fields)
        updated = record.model_copy(
            update={
                "needs_re_certification": False,
                "current_version": version,
                "updated_at": self._now(),
            }
        )
        self._store.save_workload(updated)
        return updated

    # ------------------------------------------------------------------ #
    # Certification lifecycle and admission
    # ------------------------------------------------------------------ #
    def record_certification_event(
        self, name: str, event: CertificationEvent
    ) -> WorkloadRecord:
        """Apply a certification event to a workload and persist the transition."""
        record = self.get(name)
        new_status = advance_status(record.certification_status, event)
        manifest = self._with_status(record.manifest, new_status)
        resolved = new_status in (CertificationStatus.PROVISIONAL, CertificationStatus.CERTIFIED)
        updated = record.model_copy(
            update={
                "certification_status": new_status,
                "manifest": manifest,
                "needs_re_certification": False if resolved else record.needs_re_certification,
                "production_runs_survived": _survival_after_transition(
                    record.production_runs_survived, new_status
                ),
                "updated_at": self._now(),
            }
        )
        self._store.save_workload(updated)
        return updated

    def increment_production_runs(self, name: str) -> WorkloadRecord:
        """Count a completed production run toward the certification survival gate."""
        record = self.get(name)
        updated = record.model_copy(
            update={"production_runs_survived": record.production_runs_survived + 1}
        )
        self._store.save_workload(updated)
        return updated

    def request_re_certification(self, name: str) -> WorkloadRecord:
        """Flag a workload as requiring re-certification (M26 reconcile action).

        The controller calls this when desired state changes a certification
        threshold, which :meth:`update` alone does not treat as cert-relevant.
        """
        record = self.get(name)
        updated = record.model_copy(
            update={"needs_re_certification": True, "updated_at": self._now()}
        )
        self._store.save_workload(updated)
        return updated

    def quarantine(self, name: str) -> WorkloadRecord:
        """Force a workload into quarantine, blocking staging/production admission.

        Quarantine preserves the record, its history, and its attestations; it
        only changes the certification status so the workload cannot be admitted.
        """
        record = self.get(name)
        manifest = self._with_status(record.manifest, CertificationStatus.QUARANTINED)
        updated = record.model_copy(
            update={
                "certification_status": CertificationStatus.QUARANTINED,
                "manifest": manifest,
                "updated_at": self._now(),
            }
        )
        self._store.save_workload(updated)
        return updated

    @staticmethod
    def _with_status(manifest: AgentWorkload, status: CertificationStatus) -> AgentWorkload:
        certification = manifest.spec.certification
        if certification is None:
            return manifest
        new_certification = certification.model_copy(update={"status": status})
        new_spec = manifest.spec.model_copy(update={"certification": new_certification})
        return manifest.model_copy(update={"spec": new_spec})

    def apply_attestation(
        self,
        attestation: Attestation,
        *,
        event: CertificationEvent,
        expires_at: datetime | None = None,
    ) -> WorkloadRecord:
        """Advance certification status and attach a signed attestation to the manifest."""
        record = self.get(attestation.workload_id)
        new_status = advance_status(record.certification_status, event)
        certification = record.manifest.spec.certification
        if certification is None:
            raise ValueError(
                f"workload {attestation.workload_id!r} has no certification block"
            )
        updated_certification = certification.model_copy(
            update={
                "status": new_status,
                "attestation_id": attestation.attestation_id,
                "certified_at": attestation.timestamp,
                "certified_by": attestation.signer.identity,
                "expires_at": expires_at,
            }
        )
        manifest = record.manifest.model_copy(
            update={
                "spec": record.manifest.spec.model_copy(
                    update={"certification": updated_certification}
                )
            }
        )
        resolved = new_status in (
            CertificationStatus.PROVISIONAL,
            CertificationStatus.CERTIFIED,
        )
        updated = record.model_copy(
            update={
                "certification_status": new_status,
                "manifest": manifest,
                "needs_re_certification": False if resolved else record.needs_re_certification,
                "production_runs_survived": _survival_after_transition(
                    record.production_runs_survived, new_status
                ),
                "updated_at": self._now(),
            }
        )
        self._store.save_workload(updated)
        return updated

    def check_admission(self, name: str, context: AdmissionContext) -> AdmissionDecision:
        """Evaluate admission for a target context without raising."""
        record = self.get(name)
        status = record.certification_status
        if context is AdmissionContext.SANDBOX:
            return AdmissionDecision(
                workload=name, context=context, admitted=True, actual_status=status
            )
        if context is AdmissionContext.STAGING:
            admitted = status in (CertificationStatus.PROVISIONAL, CertificationStatus.CERTIFIED)
            required = CertificationStatus.PROVISIONAL
        else:
            required = CertificationStatus.CERTIFIED
            admitted = (
                status is CertificationStatus.CERTIFIED
                and not record.needs_re_certification
                and self._has_valid_attestation(record)
            )
        reason = None
        if not admitted:
            reason = (
                f"certification status {status.value!r} is insufficient for "
                f"{context.value}; requires {required.value!r}"
            )
        return AdmissionDecision(
            workload=name,
            context=context,
            admitted=admitted,
            required_status=required,
            actual_status=status,
            reason=reason,
        )

    def require_admission(self, name: str, context: AdmissionContext) -> AdmissionDecision:
        """Like :meth:`check_admission` but raises when admission is refused."""
        decision = self.check_admission(name, context)
        if not decision.admitted:
            required = decision.required_status or CertificationStatus.CERTIFIED
            raise AdmissionRefusedError(name, context.value, required, decision.actual_status)
        return decision

    def _has_valid_attestation(self, record: WorkloadRecord) -> bool:
        certification = record.manifest.spec.certification
        if certification is None or certification.expires_at is None:
            return False
        if certification.expires_at <= self._now():
            metrics.get_metrics().record_attestation_verification(
                workload=record.name, result="failed"
            )
            return False
        if self._attestation_public_key is None or not certification.attestation_id:
            return False
        attestation = self._store.get_attestation(certification.attestation_id)
        if attestation is None or attestation.workload_id != record.name:
            metrics.get_metrics().record_attestation_verification(
                workload=record.name, result="failed"
            )
            return False
        verified = verify_attestation(attestation, self._attestation_public_key)
        metrics.get_metrics().record_attestation_verification(
            workload=record.name, result="verified" if verified else "failed"
        )
        return verified

    # ------------------------------------------------------------------ #
    # Attestations
    # ------------------------------------------------------------------ #
    def store_attestation(self, attestation: Attestation) -> Attestation:
        """Store an attestation immutably (append-only)."""
        if self._store.get_attestation(attestation.attestation_id) is not None:
            raise AttestationAlreadyExistsError(attestation.attestation_id)
        self._store.add_attestation(attestation)
        return attestation

    def get_attestation(self, attestation_id: str) -> Attestation:
        """Return an attestation after verifying its signature on read."""
        attestation = self._store.get_attestation(attestation_id)
        if attestation is None:
            raise AttestationNotFoundError(attestation_id)
        if self._attestation_public_key is None or not verify_attestation(
            attestation, self._attestation_public_key
        ):
            metrics.get_metrics().record_attestation_verification(
                workload=attestation.workload_id, result="failed"
            )
            raise AttestationVerificationError(attestation_id)
        metrics.get_metrics().record_attestation_verification(
            workload=attestation.workload_id, result="verified"
        )
        return attestation

    def list_attestations(self, name: str) -> list[Attestation]:
        """Return verified attestations for a workload."""
        self.get(name)
        attestations = self._store.list_attestations(name)
        for attestation in attestations:
            if self._attestation_public_key is None or not verify_attestation(
                attestation, self._attestation_public_key
            ):
                raise AttestationVerificationError(attestation.attestation_id)
        return attestations

    # ------------------------------------------------------------------ #
    # Tools and triggers
    # ------------------------------------------------------------------ #
    def register_tool(self, tool: ToolRecord) -> ToolRecord:
        """Register an MCP tool definition."""
        if self._store.get_tool(tool.tool_id) is not None:
            raise ToolAlreadyExistsError(tool.tool_id)
        self._store.save_tool(tool)
        return tool

    def get_tool(self, tool_id: str) -> ToolRecord:
        """Return a registered tool."""
        tool = self._store.get_tool(tool_id)
        if tool is None:
            raise UnknownToolError(tool_id)
        return tool

    def list_tools(
        self,
        *,
        trust_level: ToolTrustLevel | None = None,
        mcp_server: str | None = None,
    ) -> list[ToolRecord]:
        """List registered tools, optionally filtered."""
        tools = self._store.list_tools()
        if trust_level is not None:
            tools = [tool for tool in tools if tool.trust_level is trust_level]
        if mcp_server is not None:
            tools = [tool for tool in tools if tool.mcp_server == mcp_server]
        return tools

    def list_triggers(self, name: str) -> list[TriggerRecord]:
        """List trigger rules stored for a workload."""
        self.get(name)
        return self._store.list_triggers(name)

    def add_trigger(self, name: str, rule: TriggerRule) -> TriggerRecord:
        """Add a trigger rule to a workload."""
        self.get(name)
        trigger_id = self._next_trigger_id(name)
        record = TriggerRecord(
            trigger_id=trigger_id, workload=name, rule=rule, created_at=self._now()
        )
        self._store.add_trigger(record)
        return record

    def delete_trigger(self, name: str, trigger_id: str) -> None:
        """Remove a trigger rule from a workload."""
        self.get(name)
        if not self._store.delete_trigger(name, trigger_id):
            raise WorkloadNotFoundError(f"{name}/triggers/{trigger_id}")

    def _next_trigger_id(self, name: str) -> str:
        existing = self._store.list_triggers(name)
        return f"{name}-t{len(existing) + 1}"

    def _sync_triggers(self, manifest: AgentWorkload) -> None:
        for record in self._store.list_triggers(manifest.name):
            self._store.delete_trigger(manifest.name, record.trigger_id)
        for index, rule in enumerate(manifest.spec.triggers, start=1):
            self._store.add_trigger(
                TriggerRecord(
                    trigger_id=f"{manifest.name}-t{index}",
                    workload=manifest.name,
                    rule=rule,
                    created_at=self._now(),
                )
            )

    def _validate_tools(self, manifest: AgentWorkload) -> None:
        for entry in manifest.spec.tools.allow:
            if self._store.get_tool(entry.tool_id) is None:
                raise UnknownToolError(entry.tool_id)
            if (
                entry.trust_level is ToolTrustLevel.DESTRUCTIVE
                and not entry.require_approval
            ):
                raise DestructiveToolRequiresApprovalError(entry.tool_id)

    # ------------------------------------------------------------------ #
    # Dry-run summary
    # ------------------------------------------------------------------ #
    def enforcement_summary(self, manifest: AgentWorkload) -> EnforcementSummary:
        """Report what would be enforced for a manifest without persisting it."""
        spec = manifest.spec
        cert = spec.certification
        status = manifest.certification_status
        now = self._now()
        production_admitted = (
            status is CertificationStatus.CERTIFIED
            and cert is not None
            and bool(cert.attestation_id)
            and cert.expires_at is not None
            and cert.expires_at > now
        )

        warnings: list[str] = []
        if cert is None:
            warnings.append("no certification block; production admission is refused")
        elif not production_admitted:
            warnings.append(
                f"certification status is {status.value}; production admission is refused"
            )
        if not spec.tools.allow and not spec.tools.deny:
            warnings.append("no tools declared; default is deny-all")
        for entry in spec.tools.allow:
            if self._store.get_tool(entry.tool_id) is None:
                warnings.append(f"tool {entry.tool_id!r} is not registered")

        fan_out = spec.fan_out
        fan_out_count = (
            len(fan_out.on_completed) + len(fan_out.on_failed) + len(fan_out.on_escalation)
        )
        approvals = [
            action for action in spec.approvals.required_for if isinstance(action, ActionClass)
        ]
        return EnforcementSummary(
            valid=True,
            name=manifest.name,
            owner=manifest.owner,
            team=manifest.team,
            runtime=spec.runtime.adapter,
            certification_status=status,
            production_admitted=production_admitted,
            tools_allowed=[entry.tool_id for entry in spec.tools.allow],
            tools_denied=list(spec.tools.deny),
            approvals_required_for=approvals,
            budget_per_run_usd=spec.budget.per_run_usd,
            budget_per_day_usd=spec.budget.per_day_usd,
            sandbox_enabled=spec.sandbox.enabled if spec.sandbox else False,
            output_shaping_enabled=spec.output_shaping is not None,
            trigger_count=len(spec.triggers),
            fan_out_destinations=fan_out_count,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _coerce_runtime(value: RuntimeAdapter | str | None) -> RuntimeAdapter | None:
        if value is None or isinstance(value, RuntimeAdapter):
            return value
        try:
            return RuntimeAdapter(value)
        except ValueError as exc:
            raise ValueError(f"invalid runtime filter: {value!r}") from exc

    @staticmethod
    def _coerce_status(
        value: CertificationStatus | str | None,
    ) -> CertificationStatus | None:
        if value is None or isinstance(value, CertificationStatus):
            return value
        try:
            return CertificationStatus(value)
        except ValueError as exc:
            raise ValueError(f"invalid certification_status filter: {value!r}") from exc
