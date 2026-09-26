"""Promotion gate and re-certification tests (M32, #217-#225)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from hiveplane.api.app import create_app
from hiveplane.certification.binding import changed_bindings, compute_binding
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkResult,
    CertificationPolicy,
    CertificationRecord,
    CertificationStatus,
    Environment,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.promotion import PromotionGate
from hiveplane.certification.promotion_store import (
    InMemoryPromotionStore,
    PostgresPromotionStore,
)
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.core.workload import AgentWorkload
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from postgres import reset_database

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)
_MODEL = {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"}
_MODEL_V2 = {"provider": "openai", "family": "gpt-4o", "version": "2024-11-20"}


def _policy() -> CertificationPolicy:
    return CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.70, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85,
            max_critical_failures=0,
            max_p95_latency_ms=30000,
            min_production_runs_survived=0,
        ),
    )


def _result(workload: str, version: int) -> BenchmarkResult:
    return BenchmarkResult(
        benchmark_run_id=f"br-{workload}-{version}",
        workload_id=workload,
        manifest_version=version,
        corpus_id="corpus",
        corpus_version=1,
        model_identity="gpt-4o-2024-08-06",
        environment=_ENV,
        started_at=_NOW,
        finished_at=_NOW,
        tasks=[],
        aggregate=BenchmarkAggregate(
            total=10,
            passed=9,
            failed=1,
            pass_rate=0.90,
            critical_failures=0,
            p50_latency_ms=1000,
            p95_latency_ms=1000,
            total_tokens=0,
        ),
    )


def _setup(
    registry: RegistryService | None = None,
) -> tuple[RegistryService, CertificationService]:
    private_key, public_key = generate_keypair()
    registry = registry or RegistryService(
        InMemoryRegistryStore(), clock=lambda: _NOW, attestation_public_key=public_key
    )
    counter = itertools.count(1)
    service = CertificationService(
        CertificationEngine(_policy(), clock=lambda: _NOW),
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=lambda: _NOW,
        id_factory=lambda: f"att-{next(counter)}",
    )
    return registry, service


class _Coordinator:
    """A minimal certifier that uses the real certification service (M32-05)."""

    def __init__(self, registry: RegistryService, service: CertificationService) -> None:
        self._registry = registry
        self._service = service

    def certify(
        self,
        workload: str,
        *,
        target_context: TargetContext,
        corpus_ref: str | None = None,
        model_identity: str | None = None,
    ) -> CertificationRecord:
        version = self._registry.get(workload).current_version
        return self._service.certify_with_result(
            _result(workload, version), target_context=target_context
        )


def _gate(
    registry: RegistryService,
    *,
    coordinator: _Coordinator | None = None,
    audit: InMemoryAuditLog | None = None,
) -> PromotionGate:
    counter = itertools.count(1)
    return PromotionGate(
        registry,
        coordinator=coordinator,
        store=InMemoryPromotionStore(),
        audit=audit,
        clock=lambda: _NOW,
        id_factory=lambda: f"promo-{next(counter)}",
    )


def _certify(registry: RegistryService, service: CertificationService, workload: str) -> None:
    service.certify(_result(workload, 1), target_context=TargetContext.STAGING)
    service.certify(_result(workload, 1), target_context=TargetContext.PRODUCTION)


# --------------------------------------------------------------------------- #
# binding
# --------------------------------------------------------------------------- #
def test_binding_is_deterministic_and_names_changes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    before = compute_binding(make_manifest("repo-agent"))
    again = compute_binding(make_manifest("repo-agent"))
    assert before.artifact_hash == again.artifact_hash
    assert changed_bindings(before, again) == []

    model_change = compute_binding(
        make_manifest("repo-agent", model={"strategy": "fixed", "identity": _MODEL_V2})
    )
    changes = changed_bindings(before, model_change)
    assert any(change.startswith("model_binding") for change in changes)


def test_binding_ignores_metadata_and_certification(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    base = compute_binding(make_manifest("repo-agent"))
    other_owner = compute_binding(make_manifest("repo-agent", owner="other-team"))
    assert base.artifact_hash == other_owner.artifact_hash


def test_binding_detects_toolset_change(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    base = compute_binding(make_manifest("repo-agent"))
    tooled = compute_binding(
        make_manifest("repo-agent", tools={"deny": ["mcp.x.delete"]})
    )
    assert any(change.startswith("toolset") for change in changed_bindings(base, tooled))


# --------------------------------------------------------------------------- #
# promotion
# --------------------------------------------------------------------------- #
def test_unchanged_certified_workload_promotes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")

    promotion = _gate(registry).promote("repo-agent", 1, operator="op")

    assert promotion.promoted is True
    assert promotion.certification_id == "att-2"
    assert promotion.artifact_hash is not None


def test_changed_model_blocks_and_names_the_binding(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    registry.update(
        "repo-agent",
        make_manifest("repo-agent", model={"strategy": "fixed", "identity": _MODEL_V2}),
    )

    promotion = _gate(registry).promote("repo-agent", 2, operator="op")

    assert promotion.promoted is False
    assert any(change.startswith("model_binding") for change in promotion.changed_bindings)


def test_changed_toolset_blocks(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    registry.update(
        "repo-agent", make_manifest("repo-agent", tools={"deny": ["mcp.x.delete"]})
    )

    promotion = _gate(registry).promote("repo-agent", 2, operator="op")

    assert promotion.promoted is False
    assert any(change.startswith("toolset") for change in promotion.changed_bindings)


def test_uncertified_workload_never_promotes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, _ = _setup()
    registry.create(make_manifest("repo-agent"))

    promotion = _gate(registry).promote("repo-agent", 1, operator="op")

    assert promotion.promoted is False
    assert "not certified" in (promotion.refusal_reason or "")


def test_changed_artifact_is_invalidated_for_production(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    assert registry.get("repo-agent").certification_status is CertificationStatus.CERTIFIED

    updated = registry.update(
        "repo-agent",
        make_manifest("repo-agent", model={"strategy": "fixed", "identity": _MODEL_V2}),
    )

    assert updated.certification_status is CertificationStatus.UNCERTIFIED
    assert updated.needs_re_certification is True


def test_promotion_is_audited(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    audit = InMemoryAuditLog()
    gate = _gate(registry, audit=audit)

    refused = gate.promote("repo-agent", 1, operator="op")
    assert refused.promoted is False
    _certify(registry, service, "repo-agent")
    admitted = gate.promote("repo-agent", 1, operator="op")
    assert admitted.promoted is True

    actions = [record.action for record in audit.records()]
    assert "promotion.refused" in actions
    assert "promotion.admitted" in actions


def test_recertify_and_promote(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    gate = _gate(registry, coordinator=_Coordinator(registry, service))

    certification, promotion = gate.recertify_and_promote("repo-agent", operator="op")

    assert certification.attestation.artifact_hash is not None
    assert promotion.promoted is True


def test_promotion_records_are_listed(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    gate = _gate(registry)
    gate.promote("repo-agent", 1, operator="op")
    _certify(registry, service, "repo-agent")
    gate.promote("repo-agent", 1, operator="op")

    records = gate.list_promotions()
    assert len(records) == 2
    assert gate.get(records[0].promotion_id).workload == "repo-agent"


# --------------------------------------------------------------------------- #
# stores + migration
# --------------------------------------------------------------------------- #
def test_postgres_promotion_round_trip(
    pg_engine: Engine, make_manifest: Callable[..., AgentWorkload]
) -> None:
    reset_database(pg_engine)
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    store = PostgresPromotionStore(pg_engine)
    gate = PromotionGate(
        registry, store=store, clock=lambda: _NOW, id_factory=lambda: "promo-1"
    )
    promotion = gate.promote("repo-agent", 1, operator="op")
    assert promotion.promoted is True
    fetched = store.get_record("promo-1")
    assert fetched is not None and fetched.promoted is True
    store.clear()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_promotion_api_admits_then_refuses_changed(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    app = create_app(registry)
    client = TestClient(app)
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")

    admitted = client.post(
        "/promotions",
        json={"workload": "repo-agent", "manifest_version": 1, "operator": "op"},
    )
    assert admitted.status_code == 200
    assert admitted.json()["status"] == "promoted"

    registry.update(
        "repo-agent",
        make_manifest("repo-agent", model={"strategy": "fixed", "identity": _MODEL_V2}),
    )
    refused = client.post(
        "/promotions",
        json={"workload": "repo-agent", "manifest_version": 2, "operator": "op"},
    )
    assert refused.status_code == 409
    assert "model_binding" in refused.json()["detail"]

    assert client.get("/promotions").json()
    promotion_id = admitted.json()["promotion_id"]
    assert client.get(f"/promotions/{promotion_id}").json()["workload"] == "repo-agent"
    assert client.get("/promotions/missing").status_code == 404


def test_promotion_api_unknown_version_is_not_found(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, _ = _setup()
    registry.create(make_manifest("repo-agent"))
    client = TestClient(create_app(registry))
    response = client.post(
        "/promotions",
        json={"workload": "repo-agent", "manifest_version": 99, "operator": "op"},
    )
    assert response.status_code == 404


def test_policy_version_change_is_named(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    manifest = make_manifest("repo-agent")
    before = compute_binding(manifest, policy_version="1.0.0")
    after = compute_binding(manifest, policy_version="2.0.0")
    assert any(
        change.startswith("policy_version") for change in changed_bindings(before, after)
    )


def test_promotion_without_store_lists_empty(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, _ = _setup()
    registry.create(make_manifest("repo-agent"))
    gate = PromotionGate(registry, clock=lambda: _NOW)
    assert gate.list_promotions() == []
    with pytest.raises(KeyError):
        gate.get("missing")


def test_recertify_requires_coordinator(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, _ = _setup()
    registry.create(make_manifest("repo-agent"))
    gate = _gate(registry)
    with pytest.raises(ValueError):
        gate.recertify_and_promote("repo-agent", operator="op")


def test_promote_unknown_version_raises(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.registry.errors import VersionNotFoundError

    registry, _ = _setup()
    registry.create(make_manifest("repo-agent"))
    with pytest.raises(VersionNotFoundError):
        _gate(registry).promote("repo-agent", 99, operator="op")


def test_quarantined_workload_is_refused(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    registry.quarantine("repo-agent")

    promotion = _gate(registry).promote("repo-agent", 1, operator="op")

    assert promotion.promoted is False
    assert "quarantined" in (promotion.refusal_reason or "")


def test_stale_version_is_refused(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    registry.update("repo-agent", make_manifest("repo-agent", owner="new-team"))

    promotion = _gate(registry).promote("repo-agent", 1, operator="op")

    assert promotion.promoted is False
    assert "not the current version" in (promotion.refusal_reason or "")


def test_promotion_api_recertify(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    app = create_app(registry)
    app.state.promotion_gate = _gate(
        registry, coordinator=_Coordinator(registry, service)
    )
    client = TestClient(app)

    response = client.post("/promotions/recertify", json={"workload": "repo-agent"})

    assert response.status_code == 200
    assert response.json()["promotion"]["status"] == "promoted"
    assert response.json()["certification_id"]


def test_in_memory_promotion_store_crud(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.certification.promotion import PromotionRecord

    store = InMemoryPromotionStore()
    record = PromotionRecord(
        promotion_id="p1",
        workload="repo-agent",
        manifest_version=1,
        from_context=TargetContext.STAGING,
        to_context=TargetContext.PRODUCTION,
        status="promoted",
        operator="op",
        timestamp=_NOW,
    )
    store.save_record(record)
    assert store.get_record("p1") is not None
    assert store.get_record("missing") is None
    assert [r.promotion_id for r in store.list_records()] == ["p1"]
    store.clear()
    assert store.list_records() == []


def test_postgres_promotion_store_list_and_clear(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresPromotionStore(pg_engine)
    assert store.list_records() == []
    assert store.get_record("missing") is None
    store.clear()


def test_postgres_promotion_store_updates(
    pg_engine: Engine, make_manifest: Callable[..., AgentWorkload]
) -> None:
    from hiveplane.certification.promotion import PromotionRecord

    reset_database(pg_engine)
    store = PostgresPromotionStore(pg_engine)
    record = PromotionRecord(
        promotion_id="p1",
        workload="repo-agent",
        manifest_version=1,
        from_context=TargetContext.STAGING,
        to_context=TargetContext.PRODUCTION,
        status="refused",
        refusal_reason="nope",
        operator="op",
        timestamp=_NOW,
    )
    store.save_record(record)
    store.save_record(
        record.model_copy(update={"status": "promoted", "refusal_reason": None})
    )
    fetched = store.get_record("p1")
    assert fetched is not None and fetched.promoted is True
    assert [r.promotion_id for r in store.list_records()] == ["p1"]
    store.clear()
    assert store.list_records() == []


def test_recertify_skips_staging_when_already_certified(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    gate = _gate(registry, coordinator=_Coordinator(registry, service))

    _, promotion = gate.recertify_and_promote("repo-agent", operator="op")

    assert promotion.promoted is True


def test_policy_version_lookup_is_included_in_binding(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    # The gate uses the same resolved policy version the certification did (none),
    # so a lookup returning None must still admit.
    gate = PromotionGate(
        registry,
        policy_version_lookup=lambda workload: None,
        store=InMemoryPromotionStore(),
        clock=lambda: _NOW,
    )
    promotion = gate.promote("repo-agent", 1, operator="op")
    assert promotion.promoted is True


def test_needs_re_certification_refused(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    registry, service = _setup()
    registry.create(make_manifest("repo-agent"))
    _certify(registry, service, "repo-agent")
    registry.request_re_certification("repo-agent")

    promotion = _gate(registry).promote("repo-agent", 1, operator="op")

    assert promotion.promoted is False
    assert "re-certification" in (promotion.refusal_reason or "")


