"""Diff desired against observed fleet state (M26-02/M26-03, D22).

The differ turns a validated desired set and an observed snapshot into ordered
deltas (register, update, deregister, quarantine, enforce policy) plus the
field-level drift records that explain every divergence. It is pure: no store is
touched, so the planner can render the same result as a dry-run or an apply.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.fleet.reconcile import DesiredSpec, DriftRecord, SpecKind
from hiveplane.reconcile.conflict import (
    ConflictPolicy,
    FieldChange,
    FieldClass,
    diff_payloads,
    merge_declared_wins,
)
from hiveplane.reconcile.models import DesiredSet
from hiveplane.reconcile.observe import ObservedState

#: Field prefixes whose change requires the workload to be re-certified.
_RECERT_PREFIXES = (
    "spec.runtime",
    "spec.model",
    "spec.tools",
    "spec.certification.benchmark_corpus",
    "spec.certification.staging_threshold",
    "spec.certification.production_threshold",
)


class DeltaKind(StrEnum):
    """The kind of convergence step a delta describes."""

    CREATE = "create"
    UPDATE = "update"
    DEREGISTER = "deregister"
    QUARANTINE = "quarantine"
    ENFORCE_POLICY = "enforce_policy"


class Delta(BaseModel):
    """One convergence step the planner turns into an action."""

    model_config = ConfigDict(extra="forbid")

    kind: DeltaKind
    object_kind: SpecKind
    object_ref: str = Field(min_length=1, max_length=253)
    desired: DesiredSpec | None = None
    payload: dict[str, JsonValue] | None = None
    recert: bool = False
    reason: str | None = Field(default=None, max_length=2000)
    drifts: list[DriftRecord] = Field(default_factory=list)


class DiffResult(BaseModel):
    """The deltas and drift records produced by one diff."""

    model_config = ConfigDict(extra="forbid")

    deltas: list[Delta] = Field(default_factory=list)
    drifts: list[DriftRecord] = Field(default_factory=list)


class Differ:
    """Computes convergence deltas from desired and observed state."""

    def __init__(self, policy: ConflictPolicy | None = None) -> None:
        self._policy = policy or ConflictPolicy()

    def diff(
        self,
        desired: DesiredSet,
        observed: ObservedState,
        *,
        run_id: str,
        tenant_id: str,
        detected_at: datetime,
        previous_managed: set[str] | None = None,
        other_managed: set[str] | None = None,
    ) -> DiffResult:
        """Diff ``desired`` against ``observed`` into deltas and drift records."""
        previous = previous_managed or set()
        other = other_managed or set()
        deltas: list[Delta] = []
        drifts: list[DriftRecord] = []

        desired_workloads = {
            spec.name: spec for spec in desired.specs if spec.kind is SpecKind.WORKLOAD
        }
        for name, spec in desired_workloads.items():
            observed_payload = observed.workloads.get(name)
            if observed_payload is None:
                deltas.append(
                    Delta(
                        kind=DeltaKind.CREATE,
                        object_kind=SpecKind.WORKLOAD,
                        object_ref=name,
                        desired=spec,
                        reason="declared but not registered",
                    )
                )
                continue
            changes = diff_payloads(spec.spec, observed_payload)
            if not changes:
                continue
            workload_drifts = [
                self._drift(run_id, tenant_id, name, change, detected_at)
                for change in changes
            ]
            drifts.extend(workload_drifts)
            declarative = [
                change
                for change in changes
                if self._policy.classify(change.path) is FieldClass.DECLARATIVE
            ]
            if not declarative:
                continue
            deltas.append(
                Delta(
                    kind=DeltaKind.UPDATE,
                    object_kind=SpecKind.WORKLOAD,
                    object_ref=name,
                    desired=spec,
                    payload=merge_declared_wins(spec.spec, observed_payload, self._policy),
                    recert=any(
                        change.path.startswith(_RECERT_PREFIXES) for change in declarative
                    ),
                    reason="declared manifest differs from registered version",
                    drifts=workload_drifts,
                )
            )

        for name in observed.workloads:
            if name in desired_workloads or name in other:
                continue
            if name in previous:
                deltas.append(
                    Delta(
                        kind=DeltaKind.DEREGISTER,
                        object_kind=SpecKind.WORKLOAD,
                        object_ref=name,
                        reason="removed from desired state",
                    )
                )
            else:
                deltas.append(
                    Delta(
                        kind=DeltaKind.QUARANTINE,
                        object_kind=SpecKind.WORKLOAD,
                        object_ref=name,
                        reason="unmanaged workload outside desired state",
                    )
                )

        desired_policies = {
            spec.name: spec for spec in desired.specs if spec.kind is SpecKind.POLICY
        }
        for name, spec in desired_policies.items():
            desired_version = _policy_version(spec)
            if observed.policy_versions.get(name) != desired_version:
                deltas.append(
                    Delta(
                        kind=DeltaKind.ENFORCE_POLICY,
                        object_kind=SpecKind.POLICY,
                        object_ref=name,
                        desired=spec,
                        reason=f"observed policy version differs from {desired_version!r}",
                    )
                )

        return DiffResult(deltas=deltas, drifts=drifts)

    def _drift(
        self,
        run_id: str,
        tenant_id: str,
        object_ref: str,
        change: FieldChange,
        detected_at: datetime,
    ) -> DriftRecord:
        return DriftRecord(
            drift_id=_drift_id(run_id, object_ref, change.path),
            tenant_id=tenant_id,
            object_ref=object_ref,
            field=change.path,
            desired_value=change.desired,
            observed_value=change.observed,
            detected_at=detected_at,
            resolution=self._policy.resolution_for(change.path),
            reconcile_run_id=run_id,
        )


def _drift_id(run_id: str, object_ref: str, field: str) -> str:
    material = f"{run_id}|{object_ref}|{field}".encode()
    return f"drift-{hashlib.sha256(material).hexdigest()[:24]}"


def _policy_version(spec: DesiredSpec) -> str:
    metadata = spec.spec.get("metadata")
    if isinstance(metadata, dict):
        version = metadata.get("version")
        if version is not None:
            return str(version)
    return ""
