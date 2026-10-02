"""Tests for the worker daemon, identity, leases, and crash reclaim (M46)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.fleet.workers import WorkerCapabilities, WorkerState
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.context import SYSTEM_CONTEXT
from hiveplane.worker.daemon import WorkerDaemon
from hiveplane.worker.identity import WorkerIdentityService
from hiveplane.worker.models import (
    RunAssignment,
    StaleLeaseError,
    WorkerHeartbeatRequest,
    WorkerIdentityError,
    WorkerNotReadyError,
    WorkerRegistrationRequest,
    WorkerReport,
)
from hiveplane.worker.registry import WorkerRegistry
from hiveplane.worker.store import InMemoryWorkerStore

_T = TenantContext(tenant_id="t", role=Role.ADMIN)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _env() -> tuple[WorkerRegistry, InMemoryWorkerStore, WorkerIdentityService, _Clock]:
    clock = _Clock()
    store = InMemoryWorkerStore()
    identity = WorkerIdentityService(b"s" * 32, clock=clock)
    registry = WorkerRegistry(
        store,
        identity,
        clock=clock,
        heartbeat_timeout_s=30,
        lease_ttl_s=60,
    )
    return registry, store, identity, clock


def _register(registry: WorkerRegistry, worker_id: str = "w1", *, capacity: int = 1) -> str:
    issued = registry.enroll(worker_id, tenant_id="t")
    registry.register(
        WorkerRegistrationRequest(
            worker_id=worker_id,
            token=issued.token,
            version="0.2.0",
            capabilities=WorkerCapabilities(capacity=capacity),
        ),
        tenant_id="t",
    )
    return issued.token


# --------------------------------------------------------------------------- #
# M46-05 — worker identity
# --------------------------------------------------------------------------- #
def test_identity_issue_verify_and_binding() -> None:
    clock = _Clock()
    identity = WorkerIdentityService(b"k" * 32, clock=clock)
    issued = identity.issue("w1", "t")

    record = identity.verify(issued.token, worker_id="w1")
    assert record.worker_id == "w1" and record.tenant_id == "t"
    with pytest.raises(WorkerIdentityError):
        identity.verify(issued.token, worker_id="w2")
    with pytest.raises(WorkerIdentityError):
        identity.verify("garbage")
    forged = issued.token.rsplit(".", 1)[0] + ".AAAA"
    with pytest.raises(WorkerIdentityError):
        identity.verify(forged)


def test_identity_expiry_and_revocation() -> None:
    clock = _Clock()
    identity = WorkerIdentityService(b"k" * 32, clock=clock)
    issued = identity.issue("w1", "t", ttl_seconds=10)
    clock.advance(11)
    with pytest.raises(WorkerIdentityError):
        identity.verify(issued.token)

    fresh = identity.issue("w2", "t")
    identity.revoke(fresh.record.token_id)
    assert fresh.record.token_id in identity.revoked()
    with pytest.raises(WorkerIdentityError):
        identity.verify(fresh.token)


def test_rogue_worker_registration_is_refused() -> None:
    registry, _, _, _ = _env()
    with pytest.raises(WorkerIdentityError):
        registry.register(
            WorkerRegistrationRequest(
                worker_id="rogue", token="not-a-token", version="0.2.0"
            ),
            tenant_id="t",
        )
    # token bound to another worker cannot register
    issued = registry.enroll("w1", tenant_id="t")
    with pytest.raises(WorkerIdentityError):
        registry.register(
            WorkerRegistrationRequest(worker_id="w2", token=issued.token, version="0.2.0"),
            tenant_id="t",
        )


# --------------------------------------------------------------------------- #
# M46-02 — registration, heartbeat, liveness
# --------------------------------------------------------------------------- #
def test_registration_and_heartbeat_load_states() -> None:
    registry, store, _, clock = _env()
    token = _register(registry)

    worker = registry.heartbeat(
        "w1",
        WorkerHeartbeatRequest(token=token, running=1, max_concurrency=1),
        tenant_id="t",
    )
    assert worker.state is WorkerState.BUSY
    assert store.list_heartbeats("w1", ctx=_T)[-1].status is WorkerState.BUSY

    ready = registry.heartbeat(
        "w1",
        WorkerHeartbeatRequest(token=token, running=0, max_concurrency=1),
        tenant_id="t",
    )
    assert ready.state is WorkerState.READY

    clock.advance(31)
    assert registry.mark_unhealthy() == ["w1"]
    stale = store.get_worker("w1", "t", ctx=_T)
    assert stale is not None and stale.state is WorkerState.UNHEALTHY


def test_heartbeat_renews_active_leases() -> None:
    registry, store, _, clock = _env()
    token = _register(registry, capacity=2)
    assignment = registry.grant("w1", "run-1", workload_id="repo-agent", tenant_id="t")
    original_expiry = assignment.lease.expires_at

    clock.advance(10)
    registry.heartbeat(
        "w1",
        WorkerHeartbeatRequest(
            token=token,
            running=1,
            max_concurrency=2,
            lease_ids=[assignment.lease.lease_id],
        ),
        tenant_id="t",
    )
    renewed = store.get_lease(assignment.lease.lease_id, ctx=_T)
    assert renewed is not None and renewed.expires_at > original_expiry


# --------------------------------------------------------------------------- #
# M46-03/04 — leases, fencing, reclaim
# --------------------------------------------------------------------------- #
def test_lease_grant_accept_and_fenced_report() -> None:
    registry, store, _, _ = _env()
    token = _register(registry, capacity=1)
    assignment = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    assert registry.accept(assignment.lease.lease_id, token).run_id == "run-1"
    with pytest.raises(WorkerIdentityError):
        registry.accept(assignment.lease.lease_id, "bad")

    with pytest.raises(StaleLeaseError):
        registry.report(
            WorkerReport(
                worker_id="w1",
                lease_id=assignment.lease.lease_id,
                run_id="run-1",
                fencing_token=999,
                state=WorkerState.READY,
            ),
            token=token,
        )

    registry.report(
        WorkerReport(
            worker_id="w1",
            lease_id=assignment.lease.lease_id,
            run_id="run-1",
            fencing_token=assignment.lease.fencing_token,
            state=WorkerState.READY,
        ),
        token=token,
    )
    assert store.get_lease(assignment.lease.lease_id, ctx=_T) is None


def test_grant_refuses_non_ready_worker() -> None:
    registry, _, _, _ = _env()
    _register(registry)
    registry.drain("w1", tenant_id="t")
    with pytest.raises(WorkerNotReadyError):
        registry.grant("w1", "run-1", tenant_id="t")


def test_expired_lease_is_reassigned_with_attempt_and_attribution() -> None:
    registry, _, _, clock = _env()
    _register(registry, "w1", capacity=1)
    _register(registry, "w2", capacity=1)
    first = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    clock.advance(61)
    reassigned = registry.reclaim()

    assert len(reassigned) == 1
    assert reassigned[0].lease.worker_id == "w2"
    assert reassigned[0].lease.attempt == first.lease.attempt + 1
    assert reassigned[0].lease.fencing_token == first.lease.fencing_token + 1
    assert reassigned[0].reassigned_from == "w1"
    assert reassigned[0].workload_id == "w"


def test_crashed_worker_leases_reclaimed() -> None:
    registry, _, _, clock = _env()
    _register(registry, "w1", capacity=1)
    w2_token = _register(registry, "w2", capacity=1)
    registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    clock.advance(31)
    # w2 stays alive with a fresh heartbeat while w1 goes stale
    registry.heartbeat(
        "w2",
        WorkerHeartbeatRequest(token=w2_token, running=0, max_concurrency=1),
        tenant_id="t",
    )
    assert "w1" in registry.mark_unhealthy()
    reassigned = registry.reclaim()
    assert reassigned and reassigned[0].lease.worker_id == "w2"


# --------------------------------------------------------------------------- #
# M46-06/07 — lifecycle and fleet view
# --------------------------------------------------------------------------- #
def test_plane_reclaims_expired_lease_without_manual_call() -> None:
    from hiveplane.api.app import run_worker_reclaim_cycle
    from hiveplane.ha.leader import InMemoryLeaderStore, LeaderElector

    clock = _Clock()
    registry = WorkerRegistry(
        InMemoryWorkerStore(),
        WorkerIdentityService(b"s" * 32, clock=clock),
        clock=clock,
        heartbeat_timeout_s=3600,
        lease_ttl_s=60,
    )
    _register(registry, "w1", capacity=1)
    _register(registry, "w2", capacity=1)
    first = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    elector = LeaderElector(InMemoryLeaderStore(), leader_id="replica-a", clock=clock)
    clock.advance(61)
    reassigned = run_worker_reclaim_cycle(registry, elector)

    assert len(reassigned) == 1
    assert reassigned[0].lease.worker_id == "w2"
    assert reassigned[0].lease.attempt == first.lease.attempt + 1


def test_concurrent_reclaim_assigns_run_once() -> None:
    registry, store, _, clock = _env()
    _register(registry, "w1", capacity=1)
    _register(registry, "w2", capacity=1)
    first = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")
    clock.advance(61)

    viewed = store.get_lease(first.lease.lease_id, ctx=_T)
    assert viewed is not None
    original = store.list_all_leases
    store.list_all_leases = lambda **kwargs: [viewed]  # type: ignore[method-assign]
    try:
        first_pass = registry.reclaim()
        second_pass = registry.reclaim()
    finally:
        store.list_all_leases = original  # type: ignore[method-assign]

    assert len(first_pass) == 1
    assert second_pass == []
    live = [lease for lease in original(ctx=SYSTEM_CONTEXT) if lease.run_id == "run-1"]
    assert len(live) == 1


def test_worker_reclaim_cycle_is_leader_gated() -> None:
    from hiveplane.api.app import run_worker_reclaim_cycle
    from hiveplane.ha.leader import InMemoryLeaderStore, LeaderElector

    clock = _Clock()
    registry = WorkerRegistry(
        InMemoryWorkerStore(),
        WorkerIdentityService(b"s" * 32, clock=clock),
        clock=clock,
        heartbeat_timeout_s=3600,
        lease_ttl_s=60,
    )
    _register(registry, "w1", capacity=1)
    _register(registry, "w2", capacity=1)
    registry.grant("w1", "run-1", tenant_id="t")

    store = InMemoryLeaderStore()
    leader = LeaderElector(store, leader_id="replica-a", ttl_seconds=600, clock=clock)
    standby = LeaderElector(store, leader_id="replica-b", ttl_seconds=600, clock=clock)
    assert leader.acquire() is True

    clock.advance(61)
    assert run_worker_reclaim_cycle(registry, standby) == []
    assert len(run_worker_reclaim_cycle(registry, leader)) == 1


def test_lifecycle_drain_maintenance_resume_deregister() -> None:
    registry, store, _, _ = _env()
    _register(registry)
    assert registry.drain("w1", tenant_id="t").state is WorkerState.DRAINING
    assert registry.maintenance("w1", tenant_id="t").state is WorkerState.MAINTENANCE
    assert registry.resume("w1", tenant_id="t").state is WorkerState.READY

    registry.grant("w1", "run-1", tenant_id="t")
    registry.deregister("w1", tenant_id="t")
    assert registry.leases("w1", tenant_id="t") == []
    gone = store.get_worker("w1", "t", ctx=_T)
    assert gone is not None and gone.state is WorkerState.DEREGISTERED


def test_deregister_reassigns_inflight_leases() -> None:
    registry, _, _, _ = _env()
    _register(registry, "w1", capacity=1)
    _register(registry, "w2", capacity=1)
    first = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    registry.deregister("w1", tenant_id="t")

    assert registry.leases("w1", tenant_id="t") == []
    reassigned = registry.leases("w2", tenant_id="t")
    assert len(reassigned) == 1
    assert reassigned[0].run_id == "run-1"
    assert reassigned[0].attempt == first.lease.attempt + 1
    assert reassigned[0].fencing_token == first.lease.fencing_token + 1


def test_fleet_view_shows_load_and_leases() -> None:
    registry, _, _, _ = _env()
    _register(registry, capacity=2)
    assignment = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    fleet = registry.fleet(tenant_id="t")
    assert fleet[0].worker.worker_id == "w1"
    assert fleet[0].load == 1
    assert fleet[0].active_leases == [assignment.lease.lease_id]


# --------------------------------------------------------------------------- #
# M46-01 — daemon executes leased runs
# --------------------------------------------------------------------------- #
class _Runner:
    def __init__(self, *, succeed: bool = True) -> None:
        self.runs: list[RunAssignment] = []
        self._succeed = succeed

    def run(self, assignment: RunAssignment) -> bool:
        self.runs.append(assignment)
        return self._succeed


def test_daemon_registers_executes_and_reports() -> None:
    registry, store, _, _ = _env()
    runner = _Runner()
    daemon = WorkerDaemon(registry, worker_id="w1", token="", runner=runner, tenant_id="t")
    daemon.enroll_and_register(WorkerCapabilities(capacity=1))
    daemon.heartbeat(running=0, max_concurrency=1)
    assignment = registry.grant("w1", "run-1", workload_id="repo-agent", tenant_id="t")

    reports = daemon.tick()

    assert [a.run_id for a in runner.runs] == ["run-1"]
    assert reports[0].state is WorkerState.READY
    assert store.get_lease(assignment.lease.lease_id, ctx=_T) is None


def test_daemon_reports_failure() -> None:
    registry, _, _, _ = _env()
    daemon = WorkerDaemon(
        registry, worker_id="w1", token="", runner=_Runner(succeed=False), tenant_id="t"
    )
    daemon.enroll_and_register()
    registry.grant("w1", "run-1", tenant_id="t")
    reports = daemon.tick()
    assert reports[0].state is WorkerState.UNHEALTHY


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_postgres_worker_store_round_trip(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.core.run import AdmissionContext, Run, RunState
    from hiveplane.fleet.workers import Worker, WorkerHeartbeat, WorkerLease
    from hiveplane.persistence.run_store import PostgresRunStore
    from hiveplane.worker.models import WorkerToken
    from hiveplane.worker.registry import new_lease_id
    from hiveplane.worker.store import PostgresWorkerStore
    from postgres import ensure_schema, seed_workload

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    seed_workload(pg_engine)
    PostgresRunStore(pg_engine).save_run(
        Run(
            id="run-1",
            workload_id="agent-1",
            caller="cli",
            state=RunState.RUNNING,
            created_at=_NOW,
            updated_at=_NOW,
            context=AdmissionContext.STAGING,
        )
    )

    store = PostgresWorkerStore(pg_engine)
    store.clear()
    worker = Worker(
        worker_id="w1",
        tenant_id="default",
        state=WorkerState.READY,
        version="0.2.0",
        registered_at=_NOW,
        last_seen_at=_NOW,
    )
    lease = WorkerLease(
        lease_id=new_lease_id(),
        tenant_id="default",
        run_id="run-1",
        worker_id="w1",
        workload_id="w",
        attempt=1,
        granted_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )
    token = WorkerToken(
        token_id="wt-1",
        worker_id="w1",
        tenant_id="default",
        key_id="k",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )
    store.save_worker(worker)
    store.save_lease(lease)
    store.save_token(token)
    store.save_heartbeat(
        WorkerHeartbeat(
            heartbeat_id="hb-1",
            worker_id="w1",
            tenant_id="default",
            seen_at=_NOW,
            status=WorkerState.READY,
        )
    )
    reopened = PostgresWorkerStore(pg_engine)
    assert reopened.get_worker("w1", "default") is not None
    assert reopened.get_worker("w1", "other") is None
    assert reopened.list_workers("default")[0].worker_id == "w1"
    assert reopened.list_all_workers(ctx=SYSTEM_CONTEXT)[0].worker_id == "w1"
    assert reopened.get_lease(lease.lease_id) is not None
    assert reopened.list_all_leases(ctx=SYSTEM_CONTEXT)[0].lease_id == lease.lease_id
    assert reopened.list_leases("default", worker_id="w1")[0].run_id == "run-1"
    assert reopened.list_heartbeats("w1")[0].heartbeat_id == "hb-1"
    assert reopened.get_token("wt-1") is not None
    assert reopened.list_tokens("default")[0].worker_id == "w1"
    store.clear()


def test_postgres_worker_store_rejects_cross_tenant_id_hijack(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.fleet.workers import Worker
    from hiveplane.tenancy.errors import TenantScopeError
    from hiveplane.worker.store import PostgresWorkerStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresWorkerStore(pg_engine)
    store.clear()

    owner = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
    intruder = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)

    def _worker(worker_id: str, tenant_id: str) -> Worker:
        return Worker(
            worker_id=worker_id,
            tenant_id=tenant_id,
            state=WorkerState.READY,
            version="0.2.0",
            registered_at=_NOW,
            last_seen_at=_NOW,
        )

    store.save_worker(_worker("w1", "tenant-a"), ctx=owner)
    with pytest.raises(TenantScopeError):
        store.save_worker(_worker("w1", "tenant-b"), ctx=intruder)

    assert store.get_worker("w1", "tenant-a", ctx=owner) is not None
    assert store.get_worker("w1", "tenant-b", ctx=intruder) is None
    assert store.list_workers("tenant-b", ctx=intruder) == []
    store.clear()


def test_revoked_worker_token_rejected_on_heartbeat_and_report() -> None:
    secret = b"s" * 32
    clock = _Clock()
    store = InMemoryWorkerStore()
    registry = WorkerRegistry(
        store,
        WorkerIdentityService(secret, clock=clock),
        clock=clock,
        heartbeat_timeout_s=3600,
        lease_ttl_s=60,
    )
    issued = registry.enroll("w1", tenant_id="t")
    registry.register(
        WorkerRegistrationRequest(
            worker_id="w1", token=issued.token, version="0.2.0"
        ),
        tenant_id="t",
    )
    assignment = registry.grant("w1", "run-1", workload_id="w", tenant_id="t")

    revoked = registry.revoke_token(
        issued.record.token_id, tenant_id="t", ctx=_T
    )
    assert revoked is not None and revoked.revoked_at is not None

    with pytest.raises(WorkerIdentityError):
        registry.heartbeat(
            "w1",
            WorkerHeartbeatRequest(token=issued.token, running=0, max_concurrency=1),
            tenant_id="t",
        )
    with pytest.raises(WorkerIdentityError):
        registry.accept(assignment.lease.lease_id, issued.token)
    with pytest.raises(WorkerIdentityError):
        registry.report(
            WorkerReport(
                worker_id="w1",
                lease_id=assignment.lease.lease_id,
                run_id="run-1",
                fencing_token=assignment.lease.fencing_token,
                state=WorkerState.READY,
            ),
            token=issued.token,
        )

    # A fresh process with an empty in-memory revocation list still refuses it.
    rebuilt = WorkerRegistry(
        store,
        WorkerIdentityService(secret, clock=clock),
        clock=clock,
        heartbeat_timeout_s=3600,
        lease_ttl_s=60,
    )
    with pytest.raises(WorkerIdentityError):
        rebuilt.heartbeat(
            "w1",
            WorkerHeartbeatRequest(token=issued.token, running=0, max_concurrency=1),
            tenant_id="t",
        )


def test_workers_revoke_api_and_missing_token() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    enrolled = client.post("/workers/enroll", json={"worker_id": "w1"}).json()
    token = enrolled["token"]
    client.post(
        "/workers/register",
        json={"worker_id": "w1", "token": token, "version": "0.2.0"},
    )

    revoked = client.post(f"/workers/tokens/{enrolled['record']['token_id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    denied = client.post(
        "/workers/w1/heartbeat",
        json={"token": token, "running": 0, "max_concurrency": 1},
    )
    assert denied.status_code == 401
    assert client.post("/workers/tokens/missing/revoke").status_code == 404


def test_postgres_claim_lease_is_atomic(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.fleet.workers import Worker, WorkerLease
    from hiveplane.worker.registry import new_lease_id
    from hiveplane.worker.store import PostgresWorkerStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    writer = PostgresWorkerStore(pg_engine)
    reader = PostgresWorkerStore(pg_engine)
    writer.clear()
    writer.save_worker(
        Worker(
            worker_id="w1",
            tenant_id="default",
            state=WorkerState.READY,
            version="0.2.0",
            registered_at=_NOW,
            last_seen_at=_NOW,
        )
    )
    lease = WorkerLease(
        lease_id=new_lease_id(),
        tenant_id="default",
        run_id="run-1",
        worker_id="w1",
        attempt=1,
        granted_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )
    writer.save_lease(lease)

    claimed = writer.claim_lease(lease.lease_id, ctx=SYSTEM_CONTEXT)
    assert claimed is not None and claimed.lease_id == lease.lease_id
    assert reader.claim_lease(lease.lease_id, ctx=SYSTEM_CONTEXT) is None
    writer.clear()


def test_workers_api_flow() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    enrolled = client.post("/workers/enroll", json={"worker_id": "w1"})
    assert enrolled.status_code == 200
    token = enrolled.json()["token"]

    registered = client.post(
        "/workers/register",
        json={"worker_id": "w1", "token": token, "version": "0.2.0"},
    )
    assert registered.status_code == 201
    assert registered.json()["state"] == "ready"

    rogue = client.post(
        "/workers/register", json={"worker_id": "rogue", "token": "bad", "version": "0.2.0"}
    )
    assert rogue.status_code == 401

    heartbeat = client.post(
        "/workers/w1/heartbeat",
        json={"token": token, "running": 0, "max_concurrency": 1},
    )
    assert heartbeat.status_code == 200

    lease = client.post("/workers/w1/leases", json={"run_id": "run-1", "workload_id": "w"})
    assert lease.status_code == 200
    lease_id = lease.json()["lease"]["lease_id"]

    fleet = client.get("/workers").json()
    assert fleet[0]["worker"]["worker_id"] == "w1"
    assert fleet[0]["active_leases"] == [lease_id]

    assert client.post("/workers/w1/drain").json()["state"] == "draining"
    assert client.delete("/workers/w1").json()["state"] == "deregistered"
    assert client.post("/workers/missing/drain").status_code == 404
    missing_token = client.post("/workers/enroll", json={"worker_id": "missing"}).json()["token"]
    assert (
        client.post(
            "/workers/missing/heartbeat",
            json={"token": missing_token, "running": 0, "max_concurrency": 1},
        ).status_code
        == 404
    )


def test_worker_identity_service_rejects_empty_secret() -> None:
    with pytest.raises(WorkerIdentityError):
        WorkerIdentityService(b"")


def test_reclaim_without_healthy_worker_requeues_nothing() -> None:
    registry, _, _, clock = _env()
    _register(registry, "w1", capacity=1)
    registry.grant("w1", "run-1", tenant_id="t")
    clock.advance(61)
    assert registry.reclaim() == []


def test_register_rejects_tenant_mismatch() -> None:
    registry, _, _, _ = _env()
    issued = registry.enroll("w1", tenant_id="a")
    with pytest.raises(WorkerIdentityError):
        registry.register(
            WorkerRegistrationRequest(
                worker_id="w1", token=issued.token, version="0.2.0"
            ),
            tenant_id="b",
        )


def test_daemon_handles_runner_exception() -> None:
    class _Boom:
        def run(self, assignment: RunAssignment) -> bool:
            raise RuntimeError("boom")

    registry, _, _, _ = _env()
    daemon = WorkerDaemon(
        registry, worker_id="w1", token="", runner=_Boom(), tenant_id="t"
    )
    daemon.enroll_and_register()
    registry.grant("w1", "run-1", tenant_id="t")
    assert daemon.tick()[0].state is WorkerState.UNHEALTHY


def test_workers_api_error_branches() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    token = client.post("/workers/enroll", json={"worker_id": "w1"}).json()["token"]
    client.post("/workers/register", json={"worker_id": "w1", "token": token, "version": "0.2.0"})

    assert client.post("/workers/missing/leases", json={"run_id": "r"}).status_code == 404
    assert client.delete("/workers/missing").status_code == 404

    client.post("/workers/w1/drain")
    refused = client.post("/workers/w1/leases", json={"run_id": "r"})
    assert refused.status_code == 409


def test_postgres_worker_store_extra_paths(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.fleet.workers import Worker, WorkerLease
    from hiveplane.worker.registry import new_lease_id
    from hiveplane.worker.store import PostgresWorkerStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresWorkerStore(pg_engine)
    store.clear()
    store.save_worker(
        Worker(
            worker_id="w1",
            tenant_id="default",
            state=WorkerState.READY,
            version="0.2.0",
            registered_at=_NOW,
            last_seen_at=_NOW,
        )
    )
    lease = WorkerLease(
        lease_id=new_lease_id(),
        tenant_id="default",
        run_id="run-1",
        worker_id="w1",
        attempt=1,
        granted_at=_NOW,
        expires_at=_NOW + timedelta(seconds=60),
    )
    store.save_lease(lease)
    assert store.list_all_leases(ctx=SYSTEM_CONTEXT)[0].lease_id == lease.lease_id
    assert store.list_leases("default")[0].run_id == "run-1"
    store.delete_lease(lease.lease_id)
    assert store.get_lease(lease.lease_id) is None
    assert store.list_heartbeats("w1") == []
    assert store.get_token("missing") is None
    store.clear()
