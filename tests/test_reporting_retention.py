"""Per-tenant data retention and store deletes (M57-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hiveplane.artifacts.backend import LocalBlobBackend
from hiveplane.artifacts.service import ArtifactService, RetentionService
from hiveplane.artifacts.store import InMemoryArtifactStore
from hiveplane.auth.models import AccessEvent, AccessResult, AuthMethod, LoginEvent
from hiveplane.auth.store import InMemoryAuthStore
from hiveplane.core.run import Run, RunState
from hiveplane.cost.models import CostEvent
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.delivery.models import (
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryEventType,
    DeliveryStatus,
)
from hiveplane.delivery.store import InMemoryDeliveryStore
from hiveplane.execution.store import InMemoryRunStore, JsonFileRunStore
from hiveplane.fleet.artifacts import RetentionPolicy
from hiveplane.fleet.cost import CostType
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.reporting.models import RetentionDataClass
from hiveplane.reporting.retention import RetentionEnforcer
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy.context import SYSTEM_CONTEXT, context_for_run

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
_ACME = context_for_run("acme")
_BETA = context_for_run("beta")


def _clocked(times: list[datetime]) -> Callable[[], datetime]:
    remaining = list(times)
    return lambda: remaining.pop(0)


class _Fixture:
    def __init__(self, tmp_path: Path, *, at: datetime = _NOW) -> None:
        self.artifact_store = InMemoryArtifactStore()
        self.retention = RetentionService(
            self.artifact_store, LocalBlobBackend(tmp_path), clock=lambda: at
        )
        self.runs = InMemoryRunStore()
        self.audit = InMemoryAuditLog(clock=lambda: at)
        self.cost = InMemoryCostStore()
        self.auth = InMemoryAuthStore()
        self.delivery = InMemoryDeliveryStore()
        self.enforcer = RetentionEnforcer(
            InMemoryReportingStore(),
            self.retention,
            self.runs,
            self.audit,
            self.cost,
            self.auth,
            self.delivery,
            clock=lambda: at,
        )


def _run(
    run_id: str,
    state: RunState,
    *,
    updated_at: datetime,
    tenant_id: str = "acme",
) -> Run:
    return Run(
        id=run_id,
        workload_id="agent-1",
        caller="cli",
        state=state,
        created_at=updated_at,
        updated_at=updated_at,
        tenant_id=tenant_id,
    )


def _cost(event_id: str, occurred_at: datetime, *, tenant_id: str = "acme") -> CostEvent:
    return CostEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        team_id="team-a",
        cost_type=CostType.LLM,
        cost_usd=1.0,
        occurred_at=occurred_at,
    )


def _login(created_at: datetime, *, tenant_id: str = "acme") -> LoginEvent:
    return LoginEvent(
        tenant_id=tenant_id,
        actor="alice",
        method=AuthMethod.SESSION,
        result=AccessResult.ALLOW,
        created_at=created_at,
    )


def _access(created_at: datetime, *, tenant_id: str = "acme") -> AccessEvent:
    return AccessEvent(
        tenant_id=tenant_id,
        actor="alice",
        method=AuthMethod.SESSION,
        action="run.read",
        result=AccessResult.ALLOW,
        created_at=created_at,
    )


def _attempt(created_at: datetime, *, tenant_id: str = "acme") -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt_id=f"deliv-{tenant_id}-{created_at.isoformat()}",
        tenant_id=tenant_id,
        event_type=DeliveryEventType.COMPLETED,
        channel=DeliveryChannel.SLACK,
        target="#ops",
        status=DeliveryStatus.DELIVERED,
        created_at=created_at,
    )


def test_set_policy_is_deterministic_and_delegates(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)

    policy = fixture.enforcer.set_policy("acme", RetentionDataClass.RUNS, 30)

    assert policy.policy_id == "retention:acme:runs"
    assert policy.tenant_id == "acme"
    assert policy.data_class == "runs"
    assert policy.retain_days == 30
    assert policy.legal_hold is False
    assert fixture.enforcer.policies("acme") == [policy]

    updated = fixture.enforcer.set_policy(
        "acme", RetentionDataClass.RUNS, 7, legal_hold=True
    )
    assert updated.policy_id == policy.policy_id
    assert updated.retain_days == 7
    assert fixture.enforcer.policies("acme") == [updated]


def test_purge_due_deletes_exactly_the_expired(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fresh = _NOW - timedelta(days=1)
    fixture.enforcer.set_policy("acme", RetentionDataClass.RUNS, 5)
    fixture.enforcer.set_policy("acme", RetentionDataClass.METERING, 5)
    fixture.enforcer.set_policy("acme", RetentionDataClass.LOGS, 5)
    fixture.runs.save_run(
        _run("old-run", RunState.COMPLETED, updated_at=old), ctx=context_for_run("acme")
    )
    fixture.runs.save_run(
        _run("fresh-run", RunState.COMPLETED, updated_at=fresh), ctx=context_for_run("acme")
    )
    fixture.runs.save_run(
        _run("old-running", RunState.RUNNING, updated_at=old), ctx=context_for_run("acme")
    )
    fixture.cost.save_event(_cost("old-cost", old), ctx=_ACME)
    fixture.cost.save_event(_cost("fresh-cost", fresh), ctx=_ACME)
    fixture.auth.save_login(_login(old), ctx=_ACME)
    fixture.auth.save_access(_access(old), ctx=_ACME)
    fixture.delivery.save_attempt(_attempt(old), ctx=_ACME)

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    counts = {count.store: count.deleted for count in result.classes}
    assert counts == {"runs": 1, "metering": 1, "logs": 3}
    assert [run.id for run in fixture.runs.list_runs(ctx=context_for_run("acme"))] == [
        "old-running",
        "fresh-run",
    ]
    assert [
        event.event_id for event in fixture.cost.list_events("acme", ctx=_ACME)
    ] == ["fresh-cost"]
    assert fixture.auth.list_logins("acme", ctx=_ACME) == []
    assert fixture.delivery.list_attempts("acme", ctx=_ACME) == []
    assert result.completed_at == _NOW


def test_purge_due_is_tenant_scoped(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fixture.enforcer.set_policy("acme", RetentionDataClass.METERING, 5)
    fixture.cost.save_event(_cost("acme-old", old, tenant_id="acme"), ctx=_ACME)
    fixture.cost.save_event(_cost("beta-old", old, tenant_id="beta"), ctx=_BETA)

    result = fixture.enforcer.purge_due(at=_NOW)

    assert [count.store for count in result.classes] == ["metering"]
    assert [event.event_id for event in fixture.cost.list_events("acme", ctx=_ACME)] == []
    assert [event.event_id for event in fixture.cost.list_events("beta", ctx=_BETA)] == ["beta-old"]


def test_legal_hold_blocks_deletion(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fixture.enforcer.set_policy("acme", RetentionDataClass.METERING, 5, legal_hold=True)
    fixture.cost.save_event(_cost("old-cost", old), ctx=_ACME)

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert result.classes == []
    assert [event.event_id for event in fixture.cost.list_events("acme", ctx=_ACME)] == ["old-cost"]


def test_purge_due_prunes_audit_and_keeps_chain_verifiable(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fixture.audit = InMemoryAuditLog(clock=_clocked([old, old, _NOW, _NOW]))
    fixture.enforcer.bind_audit(fixture.audit)
    fixture.enforcer.set_policy("acme", RetentionDataClass.AUDIT, 5)
    fixture.audit.append("alice", "run.created", "run-1", ctx=context_for_run("acme"))
    fixture.audit.append("alice", "run.completed", "run-1", ctx=context_for_run("acme"))
    protected = fixture.audit.append(
        "bob", "run.created", "run-2", ctx=context_for_run("beta")
    )

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert {count.store: count.deleted for count in result.classes}["audit"] == 2
    assert fixture.audit.anchor() != "0" * 64
    assert fixture.audit.verify() is True
    remaining = fixture.audit.records(ctx=context_for_run("beta"))
    assert remaining[0].hash == protected.hash


def test_purge_due_purges_artifacts(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    artifacts = ArtifactService(
        fixture.artifact_store,
        LocalBlobBackend(tmp_path),
        clock=lambda: _NOW,
        artifact_id_factory=lambda: "art-1",
    )
    fixture.enforcer.set_policy("acme", RetentionDataClass.ARTIFACTS, 1)
    artifacts.capture(
        tenant_id="acme",
        run_id="run-1",
        filename="out.txt",
        data=b"hello",
        retention_policy_id="retention:acme:artifacts",
        ctx=_ACME,
    )
    purge_at = _NOW + timedelta(days=2)

    result = fixture.enforcer.purge_due(tenant_id="acme", at=purge_at)

    assert {count.store: count.deleted for count in result.classes} == {"artifacts": 1}
    assert artifacts.list(tenant_id="acme", ctx=_ACME) == []


def test_audit_entries_are_written_for_each_purged_class(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.enforcer.set_policy("acme", RetentionDataClass.METERING, 1)

    fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    actions = [
        record.action for record in fixture.audit.records(ctx=SYSTEM_CONTEXT)
    ]
    assert actions.count("retention.purged") == 1


def test_run_store_delete_terminal_before_honors_skip() -> None:
    store = InMemoryRunStore()
    old = _NOW - timedelta(days=10)
    store.save_run(_run("keep", RunState.COMPLETED, updated_at=old), ctx=context_for_run("acme"))
    store.save_run(
        _run("drop", RunState.COMPLETED, updated_at=old), ctx=context_for_run("acme")
    )

    deleted = store.delete_terminal_before(
        cutoff=_NOW - timedelta(days=5),
        tenant_id="acme",
        ctx=context_for_run("acme"),
        skip=lambda run_id: run_id == "keep",
    )

    assert deleted == 1
    assert store.get_run("drop", ctx=context_for_run("acme")) is None
    assert store.get_run("keep", ctx=context_for_run("acme")) is not None


def test_purge_due_skips_runs_with_artifacts(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    artifacts = ArtifactService(
        fixture.artifact_store,
        LocalBlobBackend(tmp_path),
        clock=lambda: _NOW,
        artifact_id_factory=lambda: "art-1",
    )
    old = _NOW - timedelta(days=10)
    fixture.enforcer.set_policy("acme", RetentionDataClass.RUNS, 5)
    fixture.runs.save_run(
        _run("with-artifact", RunState.COMPLETED, updated_at=old),
        ctx=context_for_run("acme"),
    )
    fixture.runs.save_run(
        _run("no-artifact", RunState.COMPLETED, updated_at=old),
        ctx=context_for_run("acme"),
    )
    artifacts.capture(
        tenant_id="acme",
        run_id="with-artifact",
        filename="out.txt",
        data=b"hello",
        ctx=_ACME,
    )

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert {count.store: count.deleted for count in result.classes} == {"runs": 1}
    assert fixture.runs.get_run("with-artifact", ctx=context_for_run("acme")) is not None
    assert fixture.runs.get_run("no-artifact", ctx=context_for_run("acme")) is None
    assert artifacts.list(tenant_id="acme", run_id="with-artifact", ctx=_ACME) != []


def test_run_store_delete_terminal_before_removes_whole_bundle() -> None:
    store = InMemoryRunStore()
    old = _NOW - timedelta(days=10)
    store.save_run(_run("old-run", RunState.FAILED, updated_at=old), ctx=context_for_run("acme"))
    store.save_run(
        _run("fresh-run", RunState.FAILED, updated_at=_NOW), ctx=context_for_run("acme")
    )

    deleted = store.delete_terminal_before(
        cutoff=_NOW - timedelta(days=5),
        tenant_id="acme",
        ctx=context_for_run("acme"),
    )

    assert deleted == 1
    assert store.get_run("old-run", ctx=context_for_run("acme")) is None
    assert store.get_run("fresh-run", ctx=context_for_run("acme")) is not None


def test_json_run_store_delete_terminal_before_removes_file(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path)
    old = _NOW - timedelta(days=10)
    store.save_run(
        _run("old-run", RunState.CANCELLED, updated_at=old), ctx=context_for_run("acme")
    )

    deleted = store.delete_terminal_before(
        cutoff=_NOW - timedelta(days=5),
        tenant_id="acme",
        ctx=context_for_run("acme"),
    )

    assert deleted == 1
    assert not list(tmp_path.glob("acme__old-run.json"))


def test_cost_store_delete_events_before_is_tenant_scoped() -> None:
    store = InMemoryCostStore()
    old = _NOW - timedelta(days=10)
    store.save_event(_cost("acme-old", old, tenant_id="acme"), ctx=_ACME)
    store.save_event(_cost("beta-old", old, tenant_id="beta"), ctx=_BETA)
    store.save_event(_cost("acme-fresh", _NOW, tenant_id="acme"), ctx=_ACME)

    assert store.delete_events_before(
        cutoff=_NOW - timedelta(days=5), tenant_id="acme", ctx=_ACME
    ) == 1
    assert [event.event_id for event in store.list_events("acme", ctx=_ACME)] == ["acme-fresh"]
    assert [event.event_id for event in store.list_events("beta", ctx=_BETA)] == ["beta-old"]


def test_auth_store_delete_events_before_covers_logins_and_access() -> None:
    store = InMemoryAuthStore()
    old = _NOW - timedelta(days=10)
    store.save_login(_login(old), ctx=_ACME)
    store.save_access(_access(old), ctx=_ACME)
    store.save_access(_access(_NOW), ctx=_ACME)

    assert (
        store.delete_events_before(cutoff=_NOW - timedelta(days=5), tenant_id="acme", ctx=_ACME)
        == 2
    )
    assert store.list_logins("acme", ctx=_ACME) == []
    assert len(store.list_access("acme", ctx=_ACME)) == 1


def test_delivery_store_delete_attempts_before() -> None:
    store = InMemoryDeliveryStore()
    old = _NOW - timedelta(days=10)
    store.save_attempt(_attempt(old), ctx=_ACME)
    store.save_attempt(_attempt(_NOW), ctx=_ACME)

    assert store.delete_attempts_before(
        cutoff=_NOW - timedelta(days=5), tenant_id="acme", ctx=_ACME
    ) == 1
    assert len(store.list_attempts("acme", ctx=_ACME)) == 1


def test_bind_audit_replaces_constructor_log(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    replacement = InMemoryAuditLog(clock=lambda: _NOW)
    fixture.enforcer.bind_audit(replacement)
    fixture.enforcer.set_policy("acme", RetentionDataClass.METERING, 1)

    fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert any(
        record.action == "retention.purged"
        for record in replacement.records(ctx=SYSTEM_CONTEXT)
    )
    assert fixture.audit.records(ctx=SYSTEM_CONTEXT) == []


def test_unknown_data_class_is_ignored(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.retention.set_policy(
        RetentionPolicy(
            policy_id="retention:acme:weird",
            tenant_id="acme",
            data_class="weird",
            retain_days=1,
        ),
        ctx=_ACME,
    )
    fixture.enforcer._tenants.add("acme")

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert result.classes == []


def test_retention_purge_runs_on_schedule(tmp_path: Path) -> None:
    """The scheduled tick purges policies persisted by another process."""
    import asyncio

    from hiveplane.api.app import _retention_tick

    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fixture.retention.set_policy(
        RetentionPolicy(
            policy_id="retention:acme:metering",
            tenant_id="acme",
            data_class=RetentionDataClass.METERING.value,
            retain_days=5,
        ),
        ctx=_ACME,
    )
    fixture.cost.save_event(_cost("old-cost", old), ctx=_ACME)

    # a freshly started process has an empty in-memory tenant set
    restarted = RetentionEnforcer(
        InMemoryReportingStore(),
        fixture.retention,
        fixture.runs,
        fixture.audit,
        fixture.cost,
        fixture.auth,
        fixture.delivery,
        clock=lambda: _NOW,
    )

    asyncio.run(_retention_tick(restarted))

    assert fixture.cost.list_events("acme", ctx=_ACME) == []


def test_audit_prefix_is_blocked_by_a_leading_foreign_record(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    old = _NOW - timedelta(days=10)
    fixture.audit = InMemoryAuditLog(clock=_clocked([old, old, old, _NOW]))
    fixture.enforcer.bind_audit(fixture.audit)
    fixture.enforcer.set_policy("acme", RetentionDataClass.AUDIT, 5)
    leading_foreign = fixture.audit.append(
        "bob", "run.created", "run-beta", ctx=context_for_run("beta")
    )
    first_expired = fixture.audit.append(
        "alice", "run.created", "run-a1", ctx=context_for_run("acme")
    )
    second_expired = fixture.audit.append(
        "alice", "run.completed", "run-a1", ctx=context_for_run("acme")
    )

    result = fixture.enforcer.purge_due(tenant_id="acme", at=_NOW)

    assert {count.store: count.deleted for count in result.classes}["audit"] == 0
    assert fixture.audit.anchor() == "0" * 64
    assert fixture.audit.verify() is True
    remaining = fixture.audit.records(ctx=SYSTEM_CONTEXT)
    assert [record.hash for record in remaining[:3]] == [
        leading_foreign.hash,
        first_expired.hash,
        second_expired.hash,
    ]
