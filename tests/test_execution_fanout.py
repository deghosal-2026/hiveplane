"""Tests for result fan-out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.fanout import FanOutService, SlackTransport, WebhookTransport
from hiveplane.execution.models import DeliveryStatus
from hiveplane.execution.store import InMemoryRunStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.COMPLETED) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        created_at=_clock(),
        updated_at=_clock(),
        finished_at=_clock(),
    )


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[FanOutDestination, dict[str, object]]] = []

    def send(self, destination: FanOutDestination, message: dict[str, Any]) -> None:
        self.sent.append((destination, message))


def _workload(
    make_manifest: Callable[..., AgentWorkload], destinations: list[dict[str, object]]
) -> AgentWorkload:
    return make_manifest(fan_out={"on_completed": destinations})


def test_notify_delivers_to_configured_transport(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    recorder = _Recorder()
    service = FanOutService(store, {FanOutType.WEBHOOK: recorder}, clock=_clock)
    workload = _workload(
        make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}]
    )
    records = service.notify(_run(), workload)
    assert [r.status for r in records] == [DeliveryStatus.DELIVERED]
    assert recorder.sent[0][1]["run_id"] == "run-1"
    assert store.list_deliveries("run-1")[0].attempts == 1


def test_notify_disabled_records_nothing(make_manifest: Callable[..., AgentWorkload]) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    service = FanOutService(store, {}, enabled=False, clock=_clock)
    workload = _workload(
        make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}]
    )
    assert service.notify(_run(), workload) == []
    assert store.list_deliveries("run-1") == []


def test_notify_includes_the_public_verification_url(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    recorder = _Recorder()
    service = FanOutService(
        store,
        {FanOutType.WEBHOOK: recorder},
        verification_base_url="https://hp.example",
        clock=_clock,
    )
    workload = make_manifest(
        fan_out={"on_completed": [{"type": "webhook", "url": "https://example.test/hook"}]},
        certification={
            "status": "certified",
            "benchmark_corpus": "corpus",
            "attestation_id": "att-1",
            "expires_at": "2026-12-31T00:00:00Z",
        },
    )

    service.notify(_run(), workload)

    assert (
        recorder.sent[0][1]["verification_url"]
        == "https://hp.example/attestations/att-1/verify"
    )


def test_notify_without_base_url_omits_verification_url(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    recorder = _Recorder()
    service = FanOutService(store, {FanOutType.WEBHOOK: recorder}, clock=_clock)
    workload = make_manifest(
        fan_out={"on_completed": [{"type": "webhook", "url": "https://example.test/hook"}]},
        certification={
            "status": "certified",
            "benchmark_corpus": "corpus",
            "attestation_id": "att-1",
            "expires_at": "2026-12-31T00:00:00Z",
        },
    )

    service.notify(_run(), workload)

    assert recorder.sent[0][1]["verification_url"] is None


def test_notify_retries_then_records_failure(make_manifest: Callable[..., AgentWorkload]) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())

    class _AlwaysFail:
        def __init__(self) -> None:
            self.attempts = 0

        def send(self, destination: FanOutDestination, message: dict[str, Any]) -> None:
            self.attempts += 1
            raise RuntimeError("boom")

    transport = _AlwaysFail()
    service = FanOutService(store, {FanOutType.WEBHOOK: transport}, max_retries=2, clock=_clock)
    workload = _workload(
        make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}]
    )
    records = service.notify(_run(), workload)
    assert records[0].status is DeliveryStatus.FAILED
    assert records[0].attempts == 3
    assert transport.attempts == 3


def test_notify_ignores_unsupported_destination_type(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    service = FanOutService(store, {}, clock=_clock)
    workload = _workload(
        make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}]
    )
    records = service.notify(_run(), workload)
    assert records[0].status is DeliveryStatus.FAILED
    assert records[0].error is not None


def test_slack_transport_posts_message() -> None:
    posted: list[tuple[str, dict[str, object]]] = []
    transport = SlackTransport(
        webhook_url="https://hooks.slack.test/x",
        post=lambda url, payload: posted.append((url, payload)),
    )
    transport.send(
        FanOutDestination(type=FanOutType.SLACK, channel="#ops"),
        {"run_id": "run-1", "state": "completed"},
    )
    assert posted[0][0] == "https://hooks.slack.test/x"
    assert "text" in posted[0][1]


def test_webhook_transport_uses_destination_url() -> None:
    posted: list[tuple[str, dict[str, object]]] = []
    transport = WebhookTransport(post=lambda url, payload: posted.append((url, payload)))
    transport.send(
        FanOutDestination(type=FanOutType.WEBHOOK, url="https://example.test/hook"),
        {"run_id": "run-1"},
    )
    assert posted[0][0] == "https://example.test/hook"


def test_slack_transport_without_url_raises() -> None:
    transport = SlackTransport(webhook_url=None)
    with pytest.raises(RuntimeError):
        transport.send(FanOutDestination(type=FanOutType.SLACK, channel="#ops"), {})


def test_webhook_transport_without_url_raises() -> None:
    transport = WebhookTransport(default_url=None)
    with pytest.raises(RuntimeError):
        transport.send(FanOutDestination(type=FanOutType.SLACK, channel="#ops"), {})
