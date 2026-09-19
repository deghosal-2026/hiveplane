"""The AdapterTaskExecutor bridge: run corpus tasks through the real agent (M23, #117).

Certification is only meaningful if the benchmark executes the workload's real
entrypoint through the normal path (adapter -> sandbox -> policy). This module
submits each corpus task as a synchronous run and maps the terminal run back to
a :class:`TaskExecution` for the deterministic check.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from hiveplane.certification.errors import CertificationError
from hiveplane.certification.models import BenchmarkTask
from hiveplane.certification.runner import (
    ReferenceExecutor,
    TaskExecution,
    TaskExecutor,
    UnconfiguredTaskExecutor,
)
from hiveplane.config import Settings
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.registry.service import RegistryService

_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


class MissingPinnedModelError(CertificationError):
    """Raised when benchmark execution is attempted without a pinned model."""

    def __init__(self) -> None:
        super().__init__(
            "benchmark execution requires a pinned model identity; "
            "pass model_identity to the executor"
        )


class AdapterTaskExecutor:
    """Executes a corpus task as a real, synchronous, sandboxed run."""

    def __init__(
        self,
        run_service: RunService,
        registry: RegistryService,
        *,
        workload: str,
        model_identity: str | None,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.05,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._run_service = run_service
        self._registry = registry
        self._workload = workload
        self._model_identity = model_identity
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep or time.sleep

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        """Submit ``task`` as a run and collect its terminal result."""
        if self._model_identity is None:
            raise MissingPinnedModelError()
        self._registry.get(self._workload)
        started = time.monotonic()
        run = self._run_service.submit(
            workload=self._workload,
            caller="benchmark",
            context=AdmissionContext.SANDBOX,
            task=dict(task.input),
            model_identity=self._model_identity,
        )
        self._run_service.start(run.id, actor="benchmark")
        current = self._wait(run.id, timeout_s=task.timeout_seconds)
        latency_ms = int((time.monotonic() - started) * 1000)
        if current.state not in _TERMINAL:
            self._run_service.intervene(run.id, InterventionAction.STOP, actor="benchmark")
            current = self._run_service.get(run.id)
        usage = self._run_service.usage(run.id)
        actions = [
            event.detail
            for event in self._run_service.events(run.id)
            if event.type is EventType.TOOL_CALL and event.detail is not None
        ]
        output = current.result if isinstance(current.result, dict) else {}
        return TaskExecution(
            output=output,
            actions=actions,
            latency_ms=latency_ms,
            tokens=sum(report.total_tokens for report in usage),
            trace_id=current.trace_id,
            model_identity=self._model_identity,
        )

    def _wait(self, run_id: str, *, timeout_s: int) -> Run:
        deadline = time.monotonic() + min(self._timeout_s, timeout_s)
        current = self._run_service.get(run_id)
        while current.state not in _TERMINAL and time.monotonic() < deadline:
            self._sleep(self._poll_interval_s)
            current = self._run_service.get(run_id)
        return current


def build_task_executor(
    settings: Settings,
    run_service: RunService,
    registry: RegistryService,
    *,
    workload: str = "",
    model_identity: str | None = None,
) -> TaskExecutor:
    """Build the benchmark executor selected by settings.

    ``adapter`` builds a real :class:`AdapterTaskExecutor` (pass the workload and
    pinned model); ``reference`` and the default return the corpus self-check and
    unconfigured executors respectively.
    """
    selected = str(settings.certification.executor)
    if selected == "adapter":
        return AdapterTaskExecutor(
            run_service,
            registry,
            workload=workload,
            model_identity=model_identity,
        )
    if selected == "reference":
        return ReferenceExecutor()
    return UnconfiguredTaskExecutor()
