"""Tests for leader election (HA) and chaos drills (M48)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.chaos import (
    ChaosEngine,
    DrillHandler,
    DrillKind,
    DrillOutcome,
    DrillRefusedError,
    DrillRequest,
    DrillScope,
    DrillVerdict,
    ensure_authorized,
)
from hiveplane.ha.leader import (
    InMemoryLeaderStore,
    LeaderElector,
    StaleLeaderError,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# M48-01/03 — leader election and fencing
# --------------------------------------------------------------------------- #
def _electors(clock: _Clock) -> tuple[LeaderElector, LeaderElector, InMemoryLeaderStore]:
    store = InMemoryLeaderStore()
    a = LeaderElector(store, leader_id="replica-a", ttl_seconds=30, clock=clock)
    b = LeaderElector(store, leader_id="replica-b", ttl_seconds=30, clock=clock)
    return a, b, store


def test_single_active_leader() -> None:
    clock = _Clock()
    a, b, _ = _electors(clock)

    assert a.acquire() is True
    assert b.acquire() is False
    assert a.is_leader() and not b.is_leader()
    assert a.epoch() == 1
    assert a.current().leader_id == "replica-a"  # type: ignore[union-attr]


def test_renew_and_release() -> None:
    clock = _Clock()
    a, b, _ = _electors(clock)
    a.acquire()
    clock.advance(10)
    assert a.renew() is True
    assert a.epoch() == 1

    a.release()
    assert b.acquire() is True
    assert b.epoch() == 2


def test_failover_without_double_reconcile() -> None:
    clock = _Clock()
    a, b, _ = _electors(clock)
    a.acquire()
    worked: list[int] = []
    assert a.run_cycle(lambda epoch: worked.append(epoch)) == None  # noqa: E711
    assert worked == [1]

    clock.advance(31)  # a's lease expires
    assert b.acquire() is True
    assert b.epoch() == 2
    # a is now stale: it must not act (no double-reconcile)
    assert a.run_cycle(lambda epoch: worked.append(epoch)) is None
    with pytest.raises(StaleLeaderError):
        a.check_fence(1)
    b.check_fence(2)
    assert worked == [1]


def test_standby_run_cycle_is_none() -> None:
    clock = _Clock()
    a, b, _ = _electors(clock)
    a.acquire()
    assert b.run_cycle(lambda epoch: "x") is None


# --------------------------------------------------------------------------- #
# M48-04/05/06 — chaos drills
# --------------------------------------------------------------------------- #
def _engine(**drills: DrillHandler) -> ChaosEngine:
    handlers: dict[DrillKind, DrillHandler] = {
        DrillKind.KILL_WORKER: drills.get(
            "kill", lambda r: DrillOutcome("lease reassigned", True)
        ),
        DrillKind.EXHAUST_BUDGET: drills.get(
            "budget", lambda r: DrillOutcome("run paused", True)
        ),
    }
    return ChaosEngine(handlers, clock=_Clock())


def test_drill_pass_and_report() -> None:
    engine = _engine()
    report = engine.run(DrillRequest(kind=DrillKind.KILL_WORKER))

    assert report.verdict is DrillVerdict.PASS
    assert "killed a worker" in report.injected
    assert report.observed == "lease reassigned"
    assert engine.report(report.drill_id) == report
    assert engine.reports()[0].drill_id == report.drill_id


def test_drill_failure_verdict() -> None:
    engine = _engine(kill=lambda r: DrillOutcome("run lost", False))
    assert engine.run(DrillRequest(kind=DrillKind.KILL_WORKER)).verdict is DrillVerdict.FAIL


def test_drill_handler_exception_is_failed_drill() -> None:
    def boom(request: DrillRequest) -> DrillOutcome:
        raise RuntimeError("kaboom")

    engine = ChaosEngine({DrillKind.KILL_WORKER: boom}, clock=_Clock())
    report = engine.run(DrillRequest(kind=DrillKind.KILL_WORKER))
    assert report.verdict is DrillVerdict.FAIL
    assert "kaboom" in report.observed


def test_unknown_drill_is_refused() -> None:
    engine = ChaosEngine({}, clock=_Clock())
    report = engine.run(DrillRequest(kind=DrillKind.REVOKE_CERT))
    assert report.verdict is DrillVerdict.REFUSED


def test_production_guardrails() -> None:
    authorized = {"ok": False}
    engine = ChaosEngine(
        {DrillKind.EXHAUST_BUDGET: lambda r: DrillOutcome("paused", True)},
        authorizer=lambda r: authorized["ok"],
        clock=_Clock(),
    )

    refused = engine.run(
        DrillRequest(kind=DrillKind.EXHAUST_BUDGET, production=True, scope_ref="prod")
    )
    assert refused.verdict is DrillVerdict.REFUSED
    assert "allow_production" in refused.notes[-1]

    no_authorizer = engine.run(
        DrillRequest(
            kind=DrillKind.EXHAUST_BUDGET,
            production=True,
            allow_production=True,
            scope_ref="prod",
        )
    )
    assert no_authorizer.verdict is DrillVerdict.REFUSED

    authorized["ok"] = True
    allowed = engine.run(
        DrillRequest(
            kind=DrillKind.EXHAUST_BUDGET,
            production=True,
            allow_production=True,
            scope_ref="prod",
            requested_by="admin",
        )
    )
    assert allowed.verdict is DrillVerdict.PASS


def test_sandbox_drill_needs_no_production_flag() -> None:
    engine = _engine()
    report = engine.run(
        DrillRequest(kind=DrillKind.EXHAUST_BUDGET, scope=DrillScope.SANDBOX)
    )
    assert report.verdict is DrillVerdict.PASS


def test_ensure_authorized() -> None:
    with pytest.raises(DrillRefusedError):
        ensure_authorized(
            DrillRequest(kind=DrillKind.KILL_WORKER, production=True), authorized=True
        )
    ensure_authorized(DrillRequest(kind=DrillKind.KILL_WORKER), authorized=False)


def test_audit_callback_records_reports() -> None:
    audited: list[str] = []
    engine = ChaosEngine(
        {DrillKind.KILL_WORKER: lambda r: DrillOutcome("recovered", True)},
        audit=lambda report: audited.append(report.drill_id),
        clock=_Clock(),
    )
    report = engine.run(DrillRequest(kind=DrillKind.KILL_WORKER))
    assert audited == [report.drill_id]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_postgres_leader_store_round_trip(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.ha.store import PostgresLeaderStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresLeaderStore(pg_engine)
    store.clear()

    a = store.try_acquire("reconcile", "replica-a", ttl_seconds=30, now=_NOW)
    assert a is not None and a.epoch == 1
    assert store.try_acquire("reconcile", "replica-b", ttl_seconds=30, now=_NOW) is None

    renewed = store.renew("reconcile", "replica-a", ttl_seconds=30, now=_NOW)
    assert renewed is not None
    assert store.renew("reconcile", "replica-b", ttl_seconds=30, now=_NOW) is None

    later = _NOW + timedelta(seconds=31)
    b = store.try_acquire("reconcile", "replica-b", ttl_seconds=30, now=later)
    assert b is not None and b.epoch == 2
    assert store.get("reconcile").leader_id == "replica-b"  # type: ignore[union-attr]

    store.release("reconcile", "replica-a")  # not the leader: no-op
    assert store.get("reconcile") is not None
    store.release("reconcile", "replica-b")
    assert store.get("reconcile") is None
    store.clear()


def test_postgres_leader_epoch_monotonic_across_release(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.ha.store import PostgresLeaderStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresLeaderStore(pg_engine)
    store.clear()

    first = store.try_acquire("reconcile", "replica-a", ttl_seconds=30, now=_NOW)
    assert first is not None and first.epoch == 1

    store.release("reconcile", "replica-a")
    assert store.get("reconcile") is None

    second = store.try_acquire("reconcile", "replica-b", ttl_seconds=30, now=_NOW)
    assert second is not None and second.epoch == 2

    leader = LeaderElector(
        store, leader_id="replica-b", ttl_seconds=30, clock=lambda: _NOW
    )
    with pytest.raises(StaleLeaderError):
        leader.check_fence(1)
    leader.check_fence(2)
    store.clear()


def test_cluster_and_chaos_api() -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    client = TestClient(create_app())
    leader = client.get("/cluster/leader")
    assert leader.status_code == 200
    assert leader.json()["leader_id"] is None

    drilled = client.post(
        "/chaos/drills", json={"kind": "inject-tool-failure", "scope_ref": "mcp.t.read"}
    )
    assert drilled.status_code == 200
    assert drilled.json()["verdict"] == "pass"
    assert client.get("/chaos/drills").json()

    refused = client.post(
        "/chaos/drills",
        json={"kind": "inject-tool-failure", "scope_ref": "prod", "production": True},
    )
    assert refused.json()["verdict"] == "refused"
