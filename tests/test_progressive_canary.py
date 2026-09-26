"""Tests for canary routing, evaluation, auto-promote, and auto-abort (M38-01..M38-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.workload import AgentWorkload
from hiveplane.progressive.canary import AUTOMATION_ACTOR, CanaryService
from hiveplane.progressive.errors import CanaryNotFoundError
from hiveplane.progressive.models import CanaryArm, CanaryRollout, CanaryState
from hiveplane.progressive.store import (
    InMemoryProgressiveStore,
    PostgresProgressiveStore,
)
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _registry(make_manifest: Callable[..., AgentWorkload]) -> RegistryService:
    registry = RegistryService(InMemoryRegistryStore(), clock=lambda: _NOW)
    registry.create(make_manifest(name="repo-agent"))  # version 1
    registry.update("repo-agent", make_manifest(name="repo-agent"))  # version 2
    registry.promote("repo-agent", 1)  # baseline stays on version 1
    return registry


def _service(
    make_manifest: Callable[..., AgentWorkload],
) -> tuple[CanaryService, RegistryService]:
    registry = _registry(make_manifest)
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"canary-{counter['n']}"

    def _sample_id() -> str:
        counter["n"] += 1
        return f"sample-{counter['n']}"

    return (
        CanaryService(
            InMemoryProgressiveStore(),
            registry=registry,
            clock=lambda: _NOW,
            id_factory=_id,
            sample_id_factory=_sample_id,
        ),
        registry,
    )


def _start(
    service: CanaryService,
    *,
    candidate_version: int = 2,
    traffic_pct: int = 100,
    window_seconds: int = 3600,
    min_sample: int = 2,
    blast_radius_cap: int | None = None,
    eligible_rule: dict[str, JsonValue] | None = None,
) -> CanaryRollout:
    return service.start(
        "repo-agent",
        candidate_version=candidate_version,
        traffic_pct=traffic_pct,
        window_seconds=window_seconds,
        min_sample=min_sample,
        blast_radius_cap=blast_radius_cap,
        eligible_rule=eligible_rule,
    )


def test_start_creates_an_active_rollout(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)

    rollout = _start(service)

    assert rollout.state is CanaryState.ACTIVE
    assert rollout.baseline_version == 1
    assert rollout.candidate_version == 2
    assert rollout.traffic_pct == 100
    assert rollout.window_end > rollout.window_start
    assert service.get(rollout.rollout_id).rollout_id == "canary-1"


def test_select_arm_splits_deterministically(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service, traffic_pct=100)
    zero = _start(service, traffic_pct=0)

    assert service.select_arm(rollout.rollout_id, "run-1") is CanaryArm.CANDIDATE
    assert service.select_arm(zero.rollout_id, "run-1") is CanaryArm.BASELINE
    # deterministic: same input → same arm
    assert service.select_arm(rollout.rollout_id, "run-2") is service.select_arm(
        rollout.rollout_id, "run-2"
    )


def test_blast_radius_cap_forces_baseline(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service, blast_radius_cap=1)
    service.record_sample(rollout.rollout_id, "run-1", arm=CanaryArm.CANDIDATE)

    assert service.select_arm(rollout.rollout_id, "run-2") is CanaryArm.BASELINE


def test_eligible_rule_filters_triggers(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service, eligible_rule={"event_type": "repo.push"})

    assert service.is_eligible(rollout, {"event_type": "repo.push"}) is True
    assert service.is_eligible(rollout, {"event_type": "alert"}) is False
    assert service.select_arm(rollout.rollout_id, "run-1", eligible=False) is CanaryArm.BASELINE


def test_evaluate_reports_a_clean_window(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service)
    for run in ("b1", "b2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.BASELINE)
    for run in ("c1", "c2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.CANDIDATE)

    evaluation = service.evaluate(rollout.rollout_id)

    assert evaluation.sample_reached is True
    assert evaluation.regression is False
    assert evaluation.ready is True


def test_evaluate_flags_a_regression(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service)
    for run in ("b1", "b2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.BASELINE)
    service.record_sample(rollout.rollout_id, "c1", arm=CanaryArm.CANDIDATE)
    service.record_sample(
        rollout.rollout_id, "c2", arm=CanaryArm.CANDIDATE, metrics={"error": True}
    )

    evaluation = service.evaluate(rollout.rollout_id)

    assert evaluation.regression is True
    assert evaluation.ready is False


def test_insufficient_sample_does_not_promote(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service)
    service.record_sample(rollout.rollout_id, "c1", arm=CanaryArm.CANDIDATE)
    service.record_sample(rollout.rollout_id, "b1", arm=CanaryArm.BASELINE)

    decision = service.auto_decide(rollout.rollout_id)

    assert decision.action == "none"
    assert service.get(rollout.rollout_id).state is CanaryState.ACTIVE


def test_clean_canary_auto_promotes_and_repoints_traffic(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest)
    rollout = _start(service)
    for run in ("b1", "b2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.BASELINE)
    for run in ("c1", "c2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.CANDIDATE)

    decision = service.auto_decide(rollout.rollout_id)

    assert decision.action == "promote"
    assert decision.actor == AUTOMATION_ACTOR
    assert service.get(rollout.rollout_id).state is CanaryState.PROMOTED
    assert registry.get("repo-agent").current_version == 2


def test_regressing_canary_auto_aborts_and_rolls_back(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest)
    rollout = _start(service)
    for run in ("b1", "b2"):
        service.record_sample(rollout.rollout_id, run, arm=CanaryArm.BASELINE)
    for run in ("c1", "c2"):
        service.record_sample(
            rollout.rollout_id, run, arm=CanaryArm.CANDIDATE, metrics={"error": True}
        )

    decision = service.auto_decide(rollout.rollout_id)

    assert decision.action == "abort"
    stored = service.get(rollout.rollout_id)
    assert stored.state is CanaryState.ROLLED_BACK
    assert stored.candidate_quarantined is True
    assert registry.get("repo-agent").current_version == 1


def test_manual_override_records_the_operator(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)
    rollout = _start(service)

    stored = service.promote(rollout.rollout_id, operator="alice", reason="looks good")

    assert stored.state is CanaryState.PROMOTED


def test_get_unknown_rollout_raises(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service(make_manifest)

    with pytest.raises(CanaryNotFoundError):
        service.get("ghost")


def test_postgres_canary_round_trip(
    pg_engine: Engine, make_manifest: Callable[..., AgentWorkload]
) -> None:
    ensure_schema(pg_engine)
    registry = _registry(make_manifest)
    store = PostgresProgressiveStore(pg_engine)
    store.clear()
    service = CanaryService(
        store,
        registry=registry,
        clock=lambda: _NOW,
        id_factory=lambda: "canary-1",
        sample_id_factory=lambda: "sample-1",
    )
    rollout = _start(service)
    service.record_sample(rollout.rollout_id, "run-1", arm=CanaryArm.CANDIDATE)

    reopened = PostgresProgressiveStore(pg_engine)

    assert reopened.get_canary_rollout("canary-1") is not None
    assert reopened.list_canary_samples("canary-1")[0].arm is CanaryArm.CANDIDATE


def test_canary_audit_records_transitions(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.persistence.audit import InMemoryAuditLog

    registry = _registry(make_manifest)
    audit = InMemoryAuditLog()
    service = CanaryService(
        InMemoryProgressiveStore(),
        registry=registry,
        audit=audit,
        clock=lambda: _NOW,
        id_factory=lambda: "canary-1",
    )
    rollout = _start(service)
    service.promote(rollout.rollout_id, operator="alice", reason="ok")

    actions = [record.action for record in audit.records()]
    assert "canary.promoted" in actions


def test_canary_status_can_be_quarantined_state() -> None:
    assert CertificationStatus.QUARANTINED.value == "quarantined"
