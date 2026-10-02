"""Unit tests for the desired-vs-observed differ (M26-02/M26-03/M26-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from hiveplane.fleet.reconcile import (
    DesiredSpec,
    DriftResolution,
    SpecKind,
    SpecSource,
)
from hiveplane.reconcile.conflict import ConflictPolicy
from hiveplane.reconcile.differ import DeltaKind, Differ, DiffResult
from hiveplane.reconcile.models import DesiredSet
from hiveplane.reconcile.observe import ObservedState

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def _manifest(name: str = "agent-1", **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
        "model": {
            "strategy": "tiered",
            "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
        },
        "certification": {
            "benchmark_corpus": "corpora/agent-1/v1",
            "staging_threshold": 0.8,
            "production_threshold": 0.9,
            "status": "uncertified",
        },
        "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
    }
    _deep_update(spec, overrides.pop("spec", {}))
    return {
        "apiVersion": "hiveplane/v1",
        "kind": "AgentWorkload",
        "metadata": {"name": name, "owner": "platform-team", "team": "platform"},
        "spec": spec,
    }


def _desired_workload(payload: dict[str, Any]) -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"workload/{payload['metadata']['name']}",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=SpecKind.WORKLOAD,
        name=payload["metadata"]["name"],
        revision="rev-1",
        content_hash="c" * 64,
        spec=payload,
        updated_at=_NOW,
    )


def _desired_policy(name: str = "platform-baseline", version: str = "3") -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"policy/{name}",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=SpecKind.POLICY,
        name=name,
        revision="rev-1",
        content_hash="d" * 64,
        spec={
            "apiVersion": "hiveplane/v1",
            "kind": "PolicyPack",
            "metadata": {"name": name, "team": "platform", "version": version},
            "spec": {"defaults": {}, "overrides": [], "approval_contacts": {}},
        },
        updated_at=_NOW,
    )


def _set(*specs: DesiredSpec) -> DesiredSet:
    return DesiredSet(
        source_id="git-main",
        source=SpecSource.GIT,
        revision="rev-1",
        specs=list(specs),
        generated_at=_NOW,
    )


def _diff(
    desired: DesiredSet,
    observed: ObservedState,
    *,
    previous_managed: set[str] | None = None,
    other_managed: set[str] | None = None,
    policy: ConflictPolicy | None = None,
) -> DiffResult:
    return Differ(policy or ConflictPolicy()).diff(
        desired,
        observed,
        run_id="rr-1",
        tenant_id="default",
        detected_at=_NOW,
        previous_managed=previous_managed or set(),
        other_managed=other_managed or set(),
    )


def test_missing_workload_is_a_create_delta() -> None:
    result = _diff(_set(_desired_workload(_manifest())), ObservedState())
    assert [delta.kind for delta in result.deltas] == [DeltaKind.CREATE]
    assert result.deltas[0].object_ref == "agent-1"
    assert result.drifts == []


def test_matching_workload_is_in_sync() -> None:
    payload = _manifest()
    observed = ObservedState(workloads={"agent-1": payload})
    result = _diff(_set(_desired_workload(payload)), observed)
    assert result.deltas == []
    assert result.drifts == []


def test_declarative_change_produces_update_and_declared_wins_drift() -> None:
    observed_payload = _manifest()
    desired_payload = _manifest(spec={"model": {"strategy": "single"}})
    result = _diff(
        _set(_desired_workload(desired_payload)),
        ObservedState(workloads={"agent-1": observed_payload}),
    )
    assert [delta.kind for delta in result.deltas] == [DeltaKind.UPDATE]
    update = result.deltas[0]
    assert update.payload is not None
    assert cast(dict[str, Any], update.payload)["spec"]["model"]["strategy"] == "single"
    assert [drift.resolution for drift in result.drifts] == [DriftResolution.DECLARED_WINS]


def test_certification_status_change_is_observed_wins_and_no_action() -> None:
    observed_payload = _manifest(spec={"certification": {"status": "certified"}})
    desired_payload = _manifest(spec={"certification": {"status": "uncertified"}})
    result = _diff(
        _set(_desired_workload(desired_payload)),
        ObservedState(workloads={"agent-1": observed_payload}),
    )
    assert result.deltas == []
    assert [drift.resolution for drift in result.drifts] == [DriftResolution.OBSERVED_WINS]


def test_threshold_change_requests_recertification() -> None:
    observed_payload = _manifest()
    desired_payload = _manifest(spec={"certification": {"production_threshold": 0.95}})
    result = _diff(
        _set(_desired_workload(desired_payload)),
        ObservedState(workloads={"agent-1": observed_payload}),
    )
    assert [delta.kind for delta in result.deltas] == [DeltaKind.UPDATE]
    assert result.deltas[0].recert is True


def test_removed_managed_workload_is_deregistered() -> None:
    observed = ObservedState(workloads={"agent-1": _manifest()})
    result = _diff(_set(), observed, previous_managed={"agent-1"})
    assert [delta.kind for delta in result.deltas] == [DeltaKind.DEREGISTER]
    assert result.deltas[0].object_ref == "agent-1"


def test_unmanaged_workload_is_quarantined() -> None:
    observed = ObservedState(workloads={"agent-1": _manifest()})
    result = _diff(_set(), observed)
    assert [delta.kind for delta in result.deltas] == [DeltaKind.QUARANTINE]


def test_workload_managed_by_another_source_is_left_alone() -> None:
    observed = ObservedState(workloads={"agent-1": _manifest()})
    result = _diff(_set(), observed, other_managed={"agent-1"})
    assert result.deltas == []


def test_policy_version_mismatch_is_enforced() -> None:
    observed = ObservedState(policy_versions={"platform-baseline": "2"})
    result = _diff(_set(_desired_policy(version="3")), observed)
    assert [delta.kind for delta in result.deltas] == [DeltaKind.ENFORCE_POLICY]


def test_policy_version_match_is_in_sync() -> None:
    observed = ObservedState(policy_versions={"platform-baseline": "3"})
    result = _diff(_set(_desired_policy(version="3")), observed)
    assert result.deltas == []
