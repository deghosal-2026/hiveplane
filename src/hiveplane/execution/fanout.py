"""Result fan-out: deliver terminal run outcomes to configured destinations."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import JsonValue

from hiveplane import telemetry
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import DeliveryRecord, DeliveryStatus
from hiveplane.execution.store import RunStore
from hiveplane.tenancy import TenantContext
from hiveplane.tenancy.context import context_for_run

Poster = Callable[[str, dict[str, Any]], None]


def _urllib_post(url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        response.read()


class DeliveryTransport(Protocol):
    """Sends a composed fan-out message to a destination."""

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None: ...


class SlackTransport:
    """Posts fan-out messages to a Slack incoming webhook."""

    def __init__(self, *, webhook_url: str | None, post: Poster | None = None) -> None:
        self._webhook_url = webhook_url
        self._post = post or _urllib_post

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
        """Post the message as Slack text."""
        if not self._webhook_url:
            raise RuntimeError("no slack webhook configured")
        self._post(self._webhook_url, {"text": json.dumps(message)})


class WebhookTransport:
    """Posts fan-out messages to a generic JSON webhook."""

    def __init__(self, *, default_url: str | None = None, post: Poster | None = None) -> None:
        self._default_url = default_url
        self._post = post or _urllib_post

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
        """Post the message to the destination URL or the default URL."""
        url = destination.url or self._default_url
        if not url:
            raise RuntimeError("no webhook url configured")
        self._post(url, dict(message))


def _target(destination: FanOutDestination) -> str:
    return destination.url or destination.channel or destination.project or destination.type.value


def _destinations(run: Run, workload: AgentWorkload) -> list[FanOutDestination]:
    fan_out = workload.spec.fan_out
    if run.state is RunState.COMPLETED:
        return list(fan_out.on_completed)
    if run.state is RunState.FAILED:
        return list(fan_out.on_failed)
    return []


def _message(run: Run, workload: AgentWorkload) -> dict[str, JsonValue]:
    certification = workload.spec.certification
    return {
        "run_id": run.id,
        "workload": run.workload_id,
        "state": run.state.value,
        "failure_reason": run.failure_reason,
        "cost_usd": run.cost_usd,
        "attestation_id": certification.attestation_id if certification else None,
    }


class FanOutService:
    """Delivers terminal run outcomes, recording every attempt."""

    def __init__(
        self,
        store: RunStore,
        transports: Mapping[FanOutType, DeliveryTransport],
        *,
        enabled: bool = True,
        max_retries: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transports = dict(transports)
        self._enabled = enabled
        self._max_retries = max_retries
        self._clock = clock or (lambda: datetime.now(UTC))

    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        """Deliver a run's outcome to every configured destination."""
        if not self._enabled:
            return []
        message = _message(run, workload)
        records: list[DeliveryRecord] = []
        for destination in _destinations(run, workload):
            records.append(self._deliver(run, workload, destination, message))
        return records

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        """Deliver an escalation notice to on_escalation destinations."""
        if not self._enabled:
            return []
        message = _message(run, workload)
        records: list[DeliveryRecord] = []
        for destination in workload.spec.fan_out.on_escalation:
            records.append(self._deliver(run, workload, destination, message))
        return records

    def _deliver(
        self,
        run: Run,
        workload: AgentWorkload,
        destination: FanOutDestination,
        message: dict[str, JsonValue],
    ) -> DeliveryRecord:
        with telemetry.span(
            "fan_out",
            run=run,
            workload=workload,
            attributes={
                "destination_type": destination.type.value,
                "target": _target(destination),
            },
        ) as active:
            record = self._attempt(run, destination, message)
            active.set_attribute("status", record.status.value)
            active.set_attribute("attempts", record.attempts)
            return record

    @staticmethod
    def _run_ctx(run: Run) -> TenantContext:
        return context_for_run(run.tenant_id, run.team_id, run.attribution_key)

    def _attempt(
        self,
        run: Run,
        destination: FanOutDestination,
        message: dict[str, JsonValue],
    ) -> DeliveryRecord:
        transport = self._transports.get(destination.type)
        target = _target(destination)
        record = DeliveryRecord(
            run_id=run.id,
            destination_type=destination.type,
            target=target,
            status=DeliveryStatus.PENDING,
            attempts=0,
            timestamp=self._clock(),
        )
        if transport is None:
            return self._finish(run, record, DeliveryStatus.FAILED, "no transport configured")
        error: str | None = None
        attempts = 0
        for _ in range(self._max_retries + 1):
            attempts += 1
            try:
                transport.send(destination, message)
            except Exception as exc:
                error = str(exc)
                continue
            delivered = record.model_copy(update={"attempts": attempts})
            return self._finish(run, delivered, DeliveryStatus.DELIVERED, None)
        return self._finish(
            run, record.model_copy(update={"attempts": attempts}), DeliveryStatus.FAILED, error
        )

    def _finish(
        self,
        run: Run,
        record: DeliveryRecord,
        status: DeliveryStatus,
        error: str | None,
    ) -> DeliveryRecord:
        finished = record.model_copy(update={"status": status, "error": error})
        self._store.add_delivery(finished, ctx=self._run_ctx(run))
        return finished
