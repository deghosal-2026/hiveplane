"""Tests for fleet-events webhooks (M56-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from hiveplane.api.app import create_app
from hiveplane.config import Settings
from hiveplane.events.models import FleetEvent, FleetEventKind
from hiveplane.events.service import FleetEventService
from hiveplane.events.store import (
    InMemoryEventSubscriptionStore,
    PostgresEventSubscriptionStore,
    build_event_subscription_store,
)
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.tenancy import Role, TenantContext
from postgres import ensure_schema

_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _service(
    sent: list[tuple[str, dict[str, Any]]], **kwargs: Any
) -> FleetEventService:
    def sender(url: str, payload: dict[str, Any]) -> None:
        sent.append((url, payload))

    return FleetEventService(
        InMemoryEventSubscriptionStore(),
        sender=sender,
        clock=lambda: _NOW,
        id_factory=lambda: "sub-1",
        **kwargs,
    )


def _event(kind: FleetEventKind, event_type: str = "run.completed") -> FleetEvent:
    return FleetEvent(
        kind=kind, event_type=event_type, tenant_id="default", occurred_at=_NOW
    )


def test_publish_delivers_only_matching_kinds() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    service = _service(sent)
    service.subscribe(
        tenant_id="default", url="https://hooks.test/run", kinds=[FleetEventKind.RUN]
    )

    delivered = service.publish(_event(FleetEventKind.RUN))
    skipped = service.publish(_event(FleetEventKind.DRIFT))

    assert delivered == ["https://hooks.test/run"]
    assert skipped == []
    assert sent[0][1]["kind"] == "run"
    assert sent[0][1]["event_type"] == "run.completed"


def test_a_failing_subscriber_does_not_raise() -> None:
    def boom(url: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("subscriber down")

    service = FleetEventService(
        InMemoryEventSubscriptionStore(),
        sender=boom,
        clock=lambda: _NOW,
        id_factory=lambda: "sub-1",
    )
    service.subscribe(
        tenant_id="default", url="https://hooks.test/x", kinds=[FleetEventKind.RUN]
    )

    assert service.publish(_event(FleetEventKind.RUN)) == []


def test_subscriptions_are_tenant_scoped() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    service = _service(sent)
    service.subscribe(
        tenant_id="acme",
        url="https://hooks.test/a",
        kinds=[FleetEventKind.RUN],
        ctx=_ACME,
    )

    assert service.subscriptions("default") == []
    assert service.publish(_event(FleetEventKind.RUN)) == []


def test_unsubscribe_stops_delivery() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    service = _service(sent)
    subscription = service.subscribe(
        tenant_id="default", url="https://hooks.test/x", kinds=[FleetEventKind.RUN]
    )

    service.unsubscribe(subscription.subscription_id, tenant_id="default")

    assert service.publish(_event(FleetEventKind.RUN)) == []
    assert service.get(subscription.subscription_id, tenant_id="default") is None


def test_subscribe_is_audited() -> None:
    audit = InMemoryAuditLog()
    sent: list[tuple[str, dict[str, Any]]] = []
    service = _service(sent, audit=audit)

    service.subscribe(
        tenant_id="default", url="https://hooks.test/x", kinds=[FleetEventKind.APPROVAL]
    )

    assert audit.records()[0].action == "events.subscribed"


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(
        build_event_subscription_store(Settings()), InMemoryEventSubscriptionStore
    )


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(
        build_event_subscription_store(settings), PostgresEventSubscriptionStore
    )


def test_postgres_subscription_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresEventSubscriptionStore(pg_engine)
    store.clear()
    service = FleetEventService(
        store, clock=lambda: _NOW, id_factory=lambda: "sub-pg"
    )
    subscription = service.subscribe(
        tenant_id="default", url="https://hooks.test/x", kinds=[FleetEventKind.RUN]
    )

    reopened = PostgresEventSubscriptionStore(pg_engine)
    assert reopened.get("sub-pg", tenant_id="default") == subscription
    assert [s.subscription_id for s in reopened.list(tenant_id="default")] == ["sub-pg"]
    store.clear()


def test_api_subscribe_list_and_delete() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app())

    created = client.post(
        "/event-subscriptions",
        json={"url": "https://hooks.test/x", "kinds": ["run", "approval"]},
    )
    assert created.status_code == 200
    subscription_id = created.json()["subscription_id"]

    listed = client.get("/event-subscriptions")
    assert listed.status_code == 200
    assert [s["subscription_id"] for s in listed.json()] == [subscription_id]

    deleted = client.delete(f"/event-subscriptions/{subscription_id}")
    assert deleted.status_code == 204
    assert client.get("/event-subscriptions").json() == []


def test_run_terminal_transition_emits_an_event(make_manifest: Any) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.core.run import RunState

    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    client = TestClient(app)
    client.post(
        "/event-subscriptions", json={"url": "https://hooks.test/run", "kinds": ["run"]}
    )
    sent: list[tuple[str, dict[str, Any]]] = []
    app.state.fleet_event_service._sender = (
        lambda url, payload: sent.append((url, payload))
    )

    run_id = client.post(
        "/runs", json={"workload": "agent-1", "caller": "cli", "context": "sandbox"}
    ).json()["id"]
    app.state.run_service.start(run_id, actor="tester")
    app.state.run_service.transition(run_id, RunState.COMPLETED, actor="tester")

    assert sent[0][0] == "https://hooks.test/run"
    assert sent[0][1]["event_type"] == "run.completed"
    assert sent[0][1]["payload"]["run_id"] == run_id


def test_approval_events_are_emitted() -> None:
    from hiveplane.core.approval import ApprovalStatus
    from hiveplane.policy.approvals import ApprovalService
    from hiveplane.policy.store import InMemoryApprovalStore

    emitted: list[Any] = []
    service = ApprovalService(
        InMemoryApprovalStore(), clock=lambda: _NOW, event_sink=emitted.append
    )

    record = service.request(
        run_id="run-1", workload="agent-1", rule="r", reason="needs approval"
    )
    service.decide(record.approval_id, status=ApprovalStatus.APPROVED, operator="alice")

    assert [event.event_type for event in emitted] == [
        "approval.requested",
        "approval.resolved",
    ]
    assert all(event.kind is FleetEventKind.APPROVAL for event in emitted)
