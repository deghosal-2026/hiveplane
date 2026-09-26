"""Reusable adapter conformance checks (M17, #45; v2 M31, #211).

Any adapter must pass :func:`assert_adapter_conforms` (the v1 lifecycle contract)
and :func:`assert_adapter_conforms_v2` (the v2 contract: capabilities, streaming,
and inference-captured model identity). A violation raises ``AssertionError`` so a
deliberately broken adapter fails the build.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from hiveplane.adapters.base import (
    Adapter,
    AdapterEventKind,
    capabilities_of,
    conformance_version_of,
    model_identity_of,
    stream_of,
)
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService


@dataclass
class Harness:
    """Everything needed to run the conformance checks against one adapter."""

    adapter: Adapter
    service: RunService
    workload: str
    model_identity: str
    held: Callable[[str], threading.Event]
    release: Callable[[str], None]


def _wait_for(service: RunService, run_id: str, state: RunState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.get(run_id).state is state:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"run {run_id!r} did not reach {state.value}; is {service.get(run_id).state.value}"
    )


def assert_adapter_conforms(harness: Harness) -> None:
    """Assert one adapter honours the full lifecycle, usage, and intervention contract."""
    adapter = harness.adapter
    service = harness.service
    workload = harness.workload
    model = harness.model_identity

    assert adapter.status("missing") is RunState.QUEUED

    run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        model_identity=model,
    )
    service.start(run.id, actor="conformance")
    _wait_for(service, run.id, RunState.COMPLETED)
    completed = service.get(run.id)
    assert completed.cost_usd > 0.0, "usage was not reported and priced"
    assert completed.sandbox is True, "sandbox context was not propagated"
    assert completed.result, "no result recorded"

    # Pause/resume/cancel-at-checkpoint are negotiated capabilities: adapters
    # that cannot pause mid-flight declare ``pause_resume=False`` and are not
    # required to honour the hold scenarios (contract v2 capability negotiation).
    if not capabilities_of(adapter).pause_resume:
        return

    paused_run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        task={"hold": True},
        model_identity=model,
    )
    service.start(paused_run.id, actor="conformance")
    assert harness.held(paused_run.id).wait(5.0), "scenario never reached its hold point"

    service.intervene(paused_run.id, InterventionAction.PAUSE, actor="op")
    assert service.get(paused_run.id).state is RunState.PAUSED
    harness.release(paused_run.id)
    _wait_for(service, paused_run.id, RunState.PAUSED)

    service.intervene(paused_run.id, InterventionAction.RESUME, actor="op")
    assert service.get(paused_run.id).state is RunState.RUNNING
    _wait_for(service, paused_run.id, RunState.COMPLETED)

    cancel_run = service.submit(
        workload=workload,
        caller="conformance",
        context=AdmissionContext.SANDBOX,
        task={"hold": True},
        model_identity=model,
    )
    service.start(cancel_run.id, actor="conformance")
    assert harness.held(cancel_run.id).wait(5.0), "scenario never reached its hold point"
    service.intervene(cancel_run.id, InterventionAction.PAUSE, actor="op")
    harness.release(cancel_run.id)
    service.intervene(cancel_run.id, InterventionAction.STOP, actor="op")
    assert service.get(cancel_run.id).state is RunState.CANCELLED


def assert_adapter_conforms_v2(
    harness: Harness,
    *,
    require_model_identity: bool = True,
    require_streaming: bool = True,
) -> None:
    """Assert one adapter honours the v2 contract (M31, #211).

    Runs the full v1 lifecycle, then verifies the versioned surface: the adapter
    reports contract v2, advertises capabilities, yields an ordered event stream,
    and reports the model identity captured from actual inference (never a
    self-report).
    """
    assert_adapter_conforms(harness)

    adapter = harness.adapter
    capabilities = capabilities_of(adapter)
    assert capabilities.contract_version == "2", "adapter does not advertise contract v2"
    assert conformance_version_of(adapter) == "2", "conformance_version() is not '2'"
    assert isinstance(capabilities.pause_resume, bool)

    run = harness.service.submit(
        workload=harness.workload,
        caller="conformance-v2",
        context=AdmissionContext.SANDBOX,
        model_identity=harness.model_identity,
    )
    harness.service.start(run.id, actor="conformance-v2")
    _wait_for(harness.service, run.id, RunState.COMPLETED)

    if require_streaming:
        events = list(stream_of(adapter, run.id))
        assert events, "stream() yielded no events"
        assert all(event.run_id == run.id for event in events), "stream event run_id mismatch"
        sequences = [event.sequence for event in events]
        assert sequences == sorted(sequences), "stream events are not ordered"
        assert events[0].kind is AdapterEventKind.STATE, "stream did not start with a state event"
        assert any(
            event.kind is AdapterEventKind.COMPLETED for event in events
        ), "stream did not report completion"

    if require_model_identity:
        assert (
            model_identity_of(adapter, run.id) == harness.model_identity
        ), "adapter did not report the model identity captured from inference"
