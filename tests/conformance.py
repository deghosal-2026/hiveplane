"""Reusable adapter conformance checks (M17, #45).

Any adapter must pass :func:`assert_adapter_conforms`. A violation raises
``AssertionError`` so a deliberately broken adapter fails the build.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from hiveplane.adapters.base import Adapter
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
