"""Tests for nine-channel fan-out, approvals, escalation, and prefs (M51)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.core.workload import AgentWorkload
from hiveplane.delivery import (
    AlreadyResolvedError,
    DeliveryChannel,
    DeliveryDestination,
    DeliveryEnvelope,
    DeliveryEventType,
    DeliveryService,
    DeliveryStatus,
    EscalationPolicy,
    EscalationService,
    InMemoryDeliveryStore,
    InteractiveApprovalService,
    InteractiveResolution,
    InvalidApprovalTokenError,
    NotificationPreference,
    render_envelope,
)
from hiveplane.delivery.channels import CHANNEL_ADAPTERS, render
from hiveplane.tenancy import Role, TenantContext

_T = TenantContext(tenant_id="t", role=Role.ADMIN)
_T1 = TenantContext(tenant_id="t1", role=Role.ADMIN)
_T2 = TenantContext(tenant_id="t2", role=Role.ADMIN)
_U = TenantContext(tenant_id="u", role=Role.ADMIN)
_OTHER = TenantContext(tenant_id="other", role=Role.ADMIN)

_NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _Recorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[DeliveryChannel, str, dict[str, object]]] = []
        self._fail = fail

    def send(self, channel: DeliveryChannel, target: str, payload: dict[str, object]) -> None:
        if self._fail:
            raise RuntimeError("transport down")
        self.sent.append((channel, target, payload))


def _envelope(
    event_type: DeliveryEventType = DeliveryEventType.COMPLETED,
) -> DeliveryEnvelope:
    return render_envelope(
        event_type,
        tenant_id="t",
        team_id="team-a",
        workload="agent-1",
        run_id="run-1",
        summary="run completed",
        base_url="https://plane.example",
        public_verify_base_url="https://verify.example",
        attestation_id="att-1",
    )


# --------------------------------------------------------------------------- #
# M51-01/02 — channels and templating
# --------------------------------------------------------------------------- #
def test_all_nine_channels_render() -> None:
    assert len(CHANNEL_ADAPTERS) == 9
    envelope = _envelope()
    for channel in DeliveryChannel:
        payload = render(channel, envelope)
        assert isinstance(payload, dict) and payload


def test_envelope_links() -> None:
    envelope = _envelope()
    assert envelope.trace_link == "https://plane.example/runs/run-1"
    assert envelope.attestation_link == "https://plane.example/attestations/att-1"
    assert envelope.public_verify_link == "https://verify.example/verify/att-1"


# --------------------------------------------------------------------------- #
# M51-01/07 — delivery and audit
# --------------------------------------------------------------------------- #
def test_delivers_to_multiple_channels_and_audits() -> None:
    store = InMemoryDeliveryStore()
    sender = _Recorder()
    service = DeliveryService(store, sender, clock=_Clock())
    destinations = [
        DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops"),
        DeliveryDestination(channel=DeliveryChannel.EMAIL, target="ops@example.com"),
        DeliveryDestination(channel=DeliveryChannel.PAGERDUTY, target="pd-key"),
    ]

    attempts = service.deliver(_envelope(), destinations)

    assert {a.channel for a in attempts} == {
        DeliveryChannel.SLACK,
        DeliveryChannel.EMAIL,
        DeliveryChannel.PAGERDUTY,
    }
    assert all(a.status is DeliveryStatus.DELIVERED for a in attempts)
    assert len(sender.sent) == 3
    assert len(service.attempts("t", ctx=_T)) == 3


def test_failed_delivery_retries_then_dead_letters() -> None:
    service = DeliveryService(
        InMemoryDeliveryStore(), _Recorder(fail=True), clock=_Clock(), max_attempts=3
    )
    attempt = service.deliver(
        _envelope(), [DeliveryDestination(channel=DeliveryChannel.WEBHOOK, target="https://x")]
    )[0]
    assert attempt.status is DeliveryStatus.DEAD_LETTER
    assert attempt.attempts == 3
    assert attempt.error is not None


# --------------------------------------------------------------------------- #
# M51-06 — notification preferences
# --------------------------------------------------------------------------- #
def test_preferences_suppress_route_batch_and_quiet_hours() -> None:
    service = DeliveryService(InMemoryDeliveryStore(), _Recorder(), clock=_Clock())
    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            events=[DeliveryEventType.FAILED],
            channels=[DeliveryChannel.SLACK],
        ),
        ctx=_T,
    )
    dest = [DeliveryDestination(channel=DeliveryChannel.EMAIL, target="x@example.com")]
    # event not in prefs
    assert service.deliver(_envelope(DeliveryEventType.COMPLETED), dest)[0].status is (
        DeliveryStatus.SUPPRESSED
    )
    # channel not in prefs
    assert service.deliver(_envelope(DeliveryEventType.FAILED), dest)[0].status is (
        DeliveryStatus.SUPPRESSED
    )

    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            channels=[DeliveryChannel.EMAIL],
            quiet_hours_start=12,
            quiet_hours_end=13,
        ),
        ctx=_T,
    )
    dest = [DeliveryDestination(channel=DeliveryChannel.EMAIL, target="x@example.com")]
    assert service.deliver(_envelope(), dest)[0].status is DeliveryStatus.SUPPRESSED
    assert service.deliver(_envelope(), dest, critical=True)[0].status is (
        DeliveryStatus.DELIVERED
    )

    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            channels=[DeliveryChannel.EMAIL],
            batch_window_s=300,
        ),
        ctx=_T,
    )
    assert service.deliver(_envelope(), dest)[0].status is DeliveryStatus.BATCHED


def test_batched_events_flush_after_window() -> None:
    clock = _Clock()
    sender = _Recorder()
    service = DeliveryService(InMemoryDeliveryStore(), sender, clock=clock)
    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            channels=[DeliveryChannel.EMAIL],
            batch_window_s=300,
        ),
        ctx=_T,
    )
    dest = [DeliveryDestination(channel=DeliveryChannel.EMAIL, target="ops@example.com")]

    attempt = service.deliver(_envelope(), dest)[0]

    assert attempt.status is DeliveryStatus.BATCHED
    assert sender.sent == []
    assert service.pending_batches() == 1

    clock.advance(301)
    flushed = service.flush_due()

    assert [item.status for item in flushed] == [DeliveryStatus.DELIVERED]
    assert len(sender.sent) == 1
    assert service.pending_batches() == 0


def test_preferences_persist_through_the_store() -> None:
    store = InMemoryDeliveryStore()
    sender = _Recorder()
    service = DeliveryService(store, sender, clock=_Clock())
    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            destinations=[
                DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops"),
                DeliveryDestination(channel=DeliveryChannel.EMAIL, target="ops@example.com"),
            ],
        ),
        ctx=_T,
    )
    service.set_preference(
        NotificationPreference(team_id="team-b", tenant_id="t"), ctx=_T
    )

    assert [item.team_id for item in store.list_preferences("t", ctx=_T)] == [
        "team-a",
        "team-b",
    ]
    assert store.get_preference("t", "missing", ctx=_T) is None

    # A fresh service over the same store sees the persisted preference and uses
    # its destinations when none are passed explicitly.
    reloaded = DeliveryService(store, sender, clock=_Clock())
    preference = reloaded.preference("team-a", "t", ctx=_T)
    assert preference is not None
    attempts = reloaded.deliver(_envelope())
    assert len(attempts) == 2
    assert {attempt.channel for attempt in attempts} == {
        DeliveryChannel.SLACK,
        DeliveryChannel.EMAIL,
    }

    store.clear()
    assert store.get_preference("t", "team-a", ctx=_T) is None


def test_preferences_are_tenant_qualified() -> None:
    store = InMemoryDeliveryStore()
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t1",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#one")],
        ),
        ctx=_T1,
    )
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t2",
            destinations=[DeliveryDestination(channel=DeliveryChannel.EMAIL, target="two@example")],
        ),
        ctx=_T2,
    )

    t1 = store.get_preference("t1", "team-a", ctx=_T1)
    t2 = store.get_preference("t2", "team-a", ctx=_T2)
    assert t1 is not None and t2 is not None
    assert t1.destinations[0].target == "#one"
    assert t2.destinations[0].target == "two@example"
    assert [item.tenant_id for item in store.list_preferences("t1", ctx=_T1)] == ["t1"]
    assert [item.tenant_id for item in store.list_preferences("t2", ctx=_T2)] == ["t2"]


def test_cross_tenant_save_does_not_overwrite() -> None:
    store = InMemoryDeliveryStore()
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t1",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#one")],
        ),
        ctx=_T1,
    )
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t2",
            destinations=[DeliveryDestination(channel=DeliveryChannel.EMAIL, target="two@example")],
        ),
        ctx=_T2,
    )

    original = store.get_preference("t1", "team-a", ctx=_T1)
    assert original is not None
    assert original.destinations[0].target == "#one"


def test_deliver_uses_only_same_tenant_preference() -> None:
    store = InMemoryDeliveryStore()
    service = DeliveryService(store, _Recorder(), clock=_Clock())
    service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="other",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#other")],
        ),
        ctx=_OTHER,
    )

    assert service.deliver(_envelope()) == []


# --------------------------------------------------------------------------- #
# M51-03/04 — interactive and mobile approvals
# --------------------------------------------------------------------------- #
def test_interactive_approval_resolves_once_and_attributes() -> None:
    store = InMemoryDeliveryStore()
    resolved: list[InteractiveResolution] = []
    service = InteractiveApprovalService(
        b"s" * 32,
        store,
        resolve=resolved.append,
        clock=_Clock(),
    )
    token = service.issue("apv-1", "run-1", tenant_id="t")

    record = service.resolve(
        token,
        decision="approve",
        operator_id="alice",
        reason="looks good",
        channel="slack",
        tenant_id="t",
        ctx=_T,
    )
    assert record.decision == "approve"
    assert record.channel == "slack"
    assert len(resolved) == 1
    assert resolved[0].approval_id == "apv-1"
    assert resolved[0].run_id == "run-1"
    assert resolved[0].tenant_id == "t"
    assert resolved[0].operator_id == "alice"
    assert resolved[0].decision == "approve"
    assert resolved[0].reason == "looks good"
    assert resolved[0].channel == "slack"
    assert service.decision("apv-1", ctx=_T) is not None

    with pytest.raises(AlreadyResolvedError):
        service.resolve(token, decision="deny", operator_id="bob", tenant_id="t")


def test_approval_token_tenant_mismatch_rejected() -> None:
    store = InMemoryDeliveryStore()
    service = InteractiveApprovalService(
        b"s" * 32, store, resolve=lambda _resolution: None, clock=_Clock()
    )
    token = service.issue("apv-1", "run-1", tenant_id="tenant-a")

    with pytest.raises(InvalidApprovalTokenError):
        service.resolve(
            token, decision="approve", operator_id="mallory", tenant_id="tenant-b"
        )


def test_replayed_token_is_rejected_by_atomic_claim() -> None:
    store = InMemoryDeliveryStore()

    assert store.claim_token("jti-1") is True
    assert store.claim_token("jti-1") is False


def test_mobile_approval_token_expiry_and_forgery() -> None:
    clock = _Clock()
    service = InteractiveApprovalService(
        b"s" * 32, InMemoryDeliveryStore(), resolve=lambda *_: None, clock=clock, ttl_seconds=60
    )
    token = service.issue("apv-1", "run-1")
    with pytest.raises(InvalidApprovalTokenError):
        service.resolve(token + "x", decision="approve", operator_id="alice")

    clock.advance(61)
    with pytest.raises(InvalidApprovalTokenError):
        service.resolve(token, decision="approve", operator_id="alice")
    with pytest.raises(InvalidApprovalTokenError):
        service.resolve(token, decision="maybe", operator_id="alice")


# --------------------------------------------------------------------------- #
# M51-05 — escalation and on-call
# --------------------------------------------------------------------------- #
def test_escalation_pages_next_operator() -> None:
    clock = _Clock()
    pages: list[tuple[str, str, str]] = []
    service = EscalationService(
        EscalationPolicy(targets=["oncall-1", "oncall-2", "oncall-3"], response_window_s=900),
        notifier=lambda approval_id, target, reason: pages.append((approval_id, target, reason)),
        clock=clock,
    )
    service.register("apv-1", tenant_id="t")
    assert pages[0][1] == "oncall-1"
    assert service.check() == []

    clock.advance(901)
    fired = service.check()
    assert [record.target for record in fired] == ["oncall-2"]
    clock.advance(901)
    assert [record.target for record in service.check()] == ["oncall-3"]

    service.respond("apv-1")
    assert service.pending() == []
    assert any(record.responded_at is not None for record in service.history())


def test_approval_request_registers_with_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiveplane.api.app import create_app
    from hiveplane.config import get_settings
    from hiveplane.core.approval import ApprovalStatus

    monkeypatch.setenv(
        "HIVEPLANE_FANOUT__ESCALATION_TARGETS", '["oncall-1","oncall-2"]'
    )
    get_settings.cache_clear()
    app = create_app()
    approval = app.state.approval_service.request(
        run_id="run-1", workload="w", rule="rule", reason="review"
    )
    escalation = app.state.escalation_service
    assert escalation is not None

    assert [item.approval_id for item in escalation.pending()] == [
        approval.approval_id
    ]

    app.state.approval_service.decide(
        approval.approval_id, status=ApprovalStatus.APPROVED, operator="alice"
    )
    assert escalation.pending() == []


def test_escalation_ticker_escalates_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiveplane.api.app import create_app
    from hiveplane.config import get_settings

    monkeypatch.setenv(
        "HIVEPLANE_FANOUT__ESCALATION_TARGETS", '["oncall-1","oncall-2"]'
    )
    get_settings.cache_clear()
    app = create_app()
    app.state.approval_service.request(
        run_id="run-1", workload="w", rule="rule", reason="review"
    )
    escalation = app.state.escalation_service
    assert escalation is not None

    base = datetime.now(UTC)
    escalation._clock = lambda: base + timedelta(seconds=99999)
    fired = escalation.check()

    assert [record.target for record in fired] == ["oncall-2"]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_postgres_delivery_store_round_trip(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.delivery.models import ApprovalDecisionRecord, DeliveryAttempt
    from hiveplane.delivery.store import PostgresDeliveryStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresDeliveryStore(pg_engine)
    store.clear()
    attempt = DeliveryAttempt(
        attempt_id="d1",
        tenant_id="t",
        event_type=DeliveryEventType.COMPLETED,
        channel=DeliveryChannel.SLACK,
        target="#ops",
        status=DeliveryStatus.DELIVERED,
        attempts=1,
        created_at=_NOW,
        delivered_at=_NOW,
    )
    decision = ApprovalDecisionRecord(
        approval_id="apv-1",
        operator_id="alice",
        decision="approve",
        tenant_id="t",
        decided_at=_NOW,
    )
    store.save_attempt(attempt, ctx=_T)
    store.save_decision(decision, ctx=_T)
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="t",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        ),
        ctx=_T,
    )
    store.save_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="u",
            destinations=[DeliveryDestination(channel=DeliveryChannel.EMAIL, target="u@example")],
        ),
        ctx=_U,
    )
    reopened = PostgresDeliveryStore(pg_engine)
    assert reopened.list_attempts("t", ctx=_T)[0].attempt_id == "d1"
    assert reopened.get_decision("apv-1", ctx=_T) is not None
    assert reopened.list_decisions("t", ctx=_T)[0].operator_id == "alice"
    assert reopened.get_decision("missing", ctx=_T) is None
    saved = reopened.get_preference("t", "team-a", ctx=_T)
    assert saved is not None
    assert saved.destinations[0].target == "#ops"
    other = reopened.get_preference("u", "team-a", ctx=_U)
    assert other is not None
    assert other.destinations[0].target == "u@example"
    assert [item.team_id for item in reopened.list_preferences("t", ctx=_T)] == ["team-a"]
    store.clear()
    assert store.get_preference("t", "team-a", ctx=_T) is None


def test_postgres_token_replay_rejected(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.delivery.store import PostgresDeliveryStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresDeliveryStore(pg_engine)
    store.clear()

    assert store.claim_token("jti-1") is True
    assert store.claim_token("jti-1") is False


def test_postgres_token_concurrent_claim_single_winner(pg_engine: object) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import Engine

    from hiveplane.delivery.store import PostgresDeliveryStore
    from postgres import ensure_schema

    assert isinstance(pg_engine, Engine)
    ensure_schema(pg_engine)
    store = PostgresDeliveryStore(pg_engine)
    store.clear()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.claim_token("jti-race"), range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_delivery_api_audit_and_resolve(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.core.approval import ApprovalStatus
    from hiveplane.core.run import AdmissionContext, RunState

    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-1"))
    runs = app.state.run_service
    run = runs.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX
    )
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="policy")
    approval = app.state.approval_service.request(
        run_id=run.id, workload="agent-1", rule="approvals.required", reason="review"
    )
    token = app.state.interactive_approvals.issue(
        approval.approval_id, run.id, tenant_id="default"
    )
    client = TestClient(app)

    resolved = client.post(
        "/delivery/approvals/resolve",
        json={"token": token, "decision": "approve", "reason": "ok"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["decision"] == "approve"
    assert resolved.json()["operator_id"] == "anonymous"
    assert (
        app.state.approval_service.get(approval.approval_id).status
        is ApprovalStatus.APPROVED
    )

    replay = client.post(
        "/delivery/approvals/resolve", json={"token": token, "decision": "deny"}
    )
    assert replay.status_code == 409
    assert client.post(
        "/delivery/approvals/resolve", json={"token": "bad.token", "decision": "approve"}
    ).status_code == 401
    assert client.get("/delivery/audit").status_code == 200


def test_interactive_resolve_applies_to_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.core.approval import ApprovalStatus
    from hiveplane.core.run import AdmissionContext, RunState

    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-1"))
    runs = app.state.run_service
    run = runs.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX
    )
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="policy")
    approval = app.state.approval_service.request(
        run_id=run.id, workload="agent-1", rule="approvals.required", reason="review"
    )
    token = app.state.interactive_approvals.issue(
        approval.approval_id, run.id, tenant_id="default"
    )

    response = TestClient(app).post(
        "/delivery/approvals/resolve", json={"token": token, "decision": "approve"}
    )

    assert response.status_code == 200
    assert (
        app.state.approval_service.get(approval.approval_id).status
        is ApprovalStatus.APPROVED
    )
    assert runs.get(run.id).state is RunState.RUNNING


def test_approval_token_run_binding_enforced(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app
    from hiveplane.core.approval import ApprovalStatus
    from hiveplane.core.run import AdmissionContext, RunState

    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-1"))
    runs = app.state.run_service
    run = runs.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX
    )
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="policy")
    approval = app.state.approval_service.request(
        run_id=run.id, workload="agent-1", rule="approvals.required", reason="review"
    )
    token = app.state.interactive_approvals.issue(
        approval.approval_id, "some-other-run", tenant_id="default"
    )

    response = TestClient(app).post(
        "/delivery/approvals/resolve", json={"token": token, "decision": "approve"}
    )

    assert response.status_code == 401
    assert (
        app.state.approval_service.get(approval.approval_id).status
        is ApprovalStatus.PENDING
    )


def test_delivery_audit_cli(monkeypatch: object) -> None:
    import json as _json

    from typer.testing import CliRunner

    from hiveplane.cli import app as cli_app

    def fake_request(method: str, url: str, payload: object = None) -> tuple[int, str]:
        return 200, _json.dumps(
            [
                {
                    "channel": "slack",
                    "target": "#ops",
                    "status": "delivered",
                    "attempts": 1,
                }
            ]
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)  # type: ignore[attr-defined]
    result = CliRunner().invoke(cli_app, ["delivery", "audit"])
    assert result.exit_code == 0
    assert "slack" in result.output


def test_envelope_includes_artifact_links() -> None:
    envelope = render_envelope(
        DeliveryEventType.COMPLETED,
        run_id="run-1",
        base_url="https://plane.example",
        artifact_ids=["art-1", "art-2"],
    )

    assert envelope.artifact_links == [
        "https://plane.example/artifacts/art-1",
        "https://plane.example/artifacts/art-2",
    ]
