"""Per-field conflict policy and payload diffing (M26-05, D22).

Conflicts resolve per field class, never globally: declarative ``spec.*`` fields
are declared-wins (git is the source of truth), while runtime/observed state and
certification status are observed-wins so the controller never asserts a status
it did not earn. Operator-pinned fields are observed-wins too, and every
divergence is still recorded as drift so it stays visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.fleet.reconcile import DriftResolution


class FieldClass(StrEnum):
    """The conflict-resolution class of a manifest field."""

    DECLARATIVE = "declarative"
    RUNTIME = "runtime"
    CERTIFICATION = "certification"
    PINNED = "pinned"


#: Certification fields the controller observes but never asserts from desired state.
_CERTIFICATION_OBSERVED = (
    "spec.certification.status",
    "spec.certification.attestation_id",
    "spec.certification.certified_at",
    "spec.certification.certified_by",
    "spec.certification.expires_at",
)

#: Runtime/observed fields that desired state must never overwrite.
_RUNTIME_FIELDS = (
    "production_runs_survived",
    "status",
    "last_seen",
    "lease",
    "heartbeat",
    "reconcile_run_id",
)


class FieldChange(BaseModel):
    """One differing leaf path between desired and observed payloads."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    desired: JsonValue = None
    observed: JsonValue = None


@dataclass(frozen=True, slots=True)
class ConflictPolicy:
    """Resolves each field path to a class and a drift resolution."""

    pinned_fields: frozenset[str] = frozenset()

    def classify(self, field: str) -> FieldClass:
        """Return the conflict class of a field path."""
        if field in self.pinned_fields:
            return FieldClass.PINNED
        if field in _CERTIFICATION_OBSERVED:
            return FieldClass.CERTIFICATION
        if field.startswith(_RUNTIME_FIELDS):
            return FieldClass.RUNTIME
        return FieldClass.DECLARATIVE

    def resolution_for(self, field: str) -> DriftResolution:
        """Return how a drift on ``field`` is resolved by default."""
        if self.classify(field) is FieldClass.DECLARATIVE:
            return DriftResolution.DECLARED_WINS
        return DriftResolution.OBSERVED_WINS


def flatten(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Flatten a nested mapping into ``path -> leaf`` entries."""
    flat: dict[str, JsonValue] = {}
    for key, value in payload.items():
        _flatten_into(flat, key, value)
    return flat


def _flatten_into(target: dict[str, JsonValue], prefix: str, value: JsonValue) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _flatten_into(target, f"{prefix}.{key}", nested)
        return
    target[prefix] = value


def diff_payloads(
    desired: Mapping[str, JsonValue], observed: Mapping[str, JsonValue]
) -> list[FieldChange]:
    """Return the leaf paths where ``desired`` differs from ``observed``."""
    desired_flat = flatten(desired)
    observed_flat = flatten(observed)
    changes: list[FieldChange] = []
    for path in sorted(set(desired_flat) | set(observed_flat)):
        desired_value = desired_flat.get(path)
        observed_value = observed_flat.get(path)
        if desired_value != observed_value:
            changes.append(
                FieldChange(path=path, desired=desired_value, observed=observed_value)
            )
    return changes


def merge_declared_wins(
    desired: Mapping[str, JsonValue],
    observed: Mapping[str, JsonValue],
    policy: ConflictPolicy,
) -> dict[str, JsonValue]:
    """Overlay desired declarative fields onto observed state.

    Observed-wins fields (runtime, certification status, pinned) keep their
    observed value; only declarative fields take the desired value.
    """
    return _merge(dict(desired), dict(observed), "", policy)


def _merge(
    desired: dict[str, JsonValue],
    observed: dict[str, JsonValue],
    prefix: str,
    policy: ConflictPolicy,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = dict(observed)
    for key, value in desired.items():
        path = f"{prefix}.{key}" if prefix else key
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _merge(value, current, path, policy)
        elif policy.classify(path) is FieldClass.DECLARATIVE:
            result[key] = value
    return result
