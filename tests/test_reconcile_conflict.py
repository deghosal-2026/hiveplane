"""Unit tests for reconcile conflict policy and payload diffing (M26-05)."""

from __future__ import annotations

from typing import Any

from hiveplane.fleet.reconcile import DriftResolution
from hiveplane.reconcile.conflict import (
    ConflictPolicy,
    FieldClass,
    diff_payloads,
    flatten,
    merge_declared_wins,
)


def _dig(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        current = current[key]
    return current


def test_classify_assigns_field_classes() -> None:
    policy = ConflictPolicy(pinned_fields=frozenset({"spec.budget.per_day_usd"}))
    assert policy.classify("spec.runtime.adapter") is FieldClass.DECLARATIVE
    assert policy.classify("metadata.team") is FieldClass.DECLARATIVE
    assert policy.classify("spec.certification.status") is FieldClass.CERTIFICATION
    assert policy.classify("spec.certification.attestation_id") is FieldClass.CERTIFICATION
    assert policy.classify("production_runs_survived") is FieldClass.RUNTIME
    assert policy.classify("spec.budget.per_day_usd") is FieldClass.PINNED


def test_resolution_for_maps_to_drift_resolution() -> None:
    policy = ConflictPolicy()
    assert policy.resolution_for("spec.model.strategy") is DriftResolution.DECLARED_WINS
    assert policy.resolution_for("spec.certification.status") is DriftResolution.OBSERVED_WINS


def test_flatten_walks_nested_leaves() -> None:
    assert flatten({"a": {"b": 1, "c": {"d": "x"}}, "e": True}) == {
        "a.b": 1,
        "a.c.d": "x",
        "e": True,
    }


def test_diff_payloads_reports_changed_leaf_paths() -> None:
    desired: dict[str, Any] = {"spec": {"model": {"strategy": "tiered"}, "tools": {"allow": []}}}
    observed: dict[str, Any] = {"spec": {"model": {"strategy": "single"}, "tools": {"allow": []}}}
    changes = diff_payloads(desired, observed)
    assert [change.path for change in changes] == ["spec.model.strategy"]
    assert changes[0].desired == "tiered"
    assert changes[0].observed == "single"


def test_merge_declared_wins_keeps_observed_runtime_and_cert_fields() -> None:
    policy = ConflictPolicy()
    desired: dict[str, Any] = {
        "spec": {
            "model": {"strategy": "tiered"},
            "certification": {"status": "uncertified", "production_threshold": 0.9},
        }
    }
    observed: dict[str, Any] = {
        "spec": {
            "model": {"strategy": "single"},
            "certification": {"status": "certified", "production_threshold": 0.8},
        }
    }
    merged = merge_declared_wins(desired, observed, policy)
    assert _dig(merged, "spec", "model", "strategy") == "tiered"
    assert _dig(merged, "spec", "certification", "status") == "certified"
    assert _dig(merged, "spec", "certification", "production_threshold") == 0.9


def test_merge_ignores_pinned_fields() -> None:
    policy = ConflictPolicy(pinned_fields=frozenset({"spec.model.strategy"}))
    desired: dict[str, Any] = {"spec": {"model": {"strategy": "tiered"}}}
    observed: dict[str, Any] = {"spec": {"model": {"strategy": "single"}}}
    merged = merge_declared_wins(desired, observed, policy)
    assert _dig(merged, "spec", "model", "strategy") == "single"
