"""Tests for the production shadow runner over the run service (M37-01)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.service import RunService
from hiveplane.progressive.errors import ShadowBudgetExceededError
from hiveplane.progressive.models import ShadowStatus
from hiveplane.progressive.runner import RunShadowRunner
from hiveplane.progressive.shadow import ShadowService
from hiveplane.progressive.store import InMemoryProgressiveStore
from test_execution_service import _service
from test_progressive_shadow import _run

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _completing(
    make_manifest: Callable[..., AgentWorkload],
    *,
    result: Any = None,
    cost: float = 0.0,
) -> RunService:
    """A run service whose executor completes the run (and bills it) on start."""
    runs, executor, _, _ = _service(make_manifest)
    completed_result = {"ok": True} if result is None else result

    def start(context: Any) -> None:
        executor.started.append(context.run.id)
        if cost:
            runs.record_usage(
                context.run.id,
                UsageReport(
                    run_id=context.run.id,
                    input_tokens=0,
                    output_tokens=0,
                    tool_calls=0,
                    cost_usd=cost,
                    timestamp=_NOW,
                ),
            )
        runs.transition(
            context.run.id, RunState.COMPLETED, actor="adapter", result=completed_result
        )

    executor.start = start  # type: ignore[method-assign]
    return runs


def test_run_shadow_runner_mirrors_task_and_is_isolated(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs = _completing(make_manifest)
    runner = RunShadowRunner(runs)
    production = _run()

    outcome = runner.run(
        production,
        candidate_workload_id="agent-1",
        candidate_version=None,
        budget_id="shadow",
    )

    shadow = next(
        run for run in runs.list_runs(workload="agent-1") if run.shadow_of == production.id
    )
    assert shadow.task == production.task
    assert shadow.read_only is True
    assert shadow.context is not None and shadow.context.value == "sandbox"
    assert outcome.status in {ShadowStatus.COMPLETED, ShadowStatus.FAILED}
    assert outcome.cost_usd == 0.0


class _DeferredRuns:
    """A run-service stub whose run only reaches terminal on a later poll."""

    def __init__(self, states: list[RunState]) -> None:
        self._states = list(states)
        self.get_calls = 0
        self._run = Run(
            id="shadow-run",
            workload_id="agent-1",
            caller="progressive-delivery",
            state=RunState.QUEUED,
            context=AdmissionContext.SANDBOX,
            task={"ticket": "T-1"},
            result={"ok": True, "variant": "candidate"},
            cost_usd=0.05,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
            finished_at=_NOW,
        )

    def submit(self, **kwargs: object) -> Run:
        return self._run

    def start(self, run_id: str, **kwargs: object) -> Run:
        self._run = self._run.model_copy(update={"state": RunState.RUNNING})
        return self._run

    def get(self, run_id: str, **kwargs: object) -> Run:
        self.get_calls += 1
        state = self._states.pop(0) if self._states else RunState.COMPLETED
        self._run = self._run.model_copy(update={"state": state})
        return self._run

    def events(self, run_id: str, **kwargs: object) -> list[Any]:
        return []


def test_run_shadow_runner_waits_for_terminal_state() -> None:
    runs = _DeferredRuns([RunState.RUNNING, RunState.COMPLETED])
    runner = RunShadowRunner(
        runs,  # type: ignore[arg-type]
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    outcome = runner.run(
        _run(),
        candidate_workload_id="agent-1",
        candidate_version=None,
        budget_id="shadow",
    )

    assert runs.get_calls >= 2  # polled while the run was still RUNNING
    assert outcome.status is ShadowStatus.COMPLETED
    assert outcome.result == {"ok": True, "variant": "candidate"}
    assert outcome.cost_usd == pytest.approx(0.05)


def test_shadow_budget_cap_uses_recorded_run_cost(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs = _completing(make_manifest, result={"ok": True}, cost=0.10)
    service = ShadowService(
        InMemoryProgressiveStore(),
        runner=RunShadowRunner(runs),
        budget_cap_usd=0.10,
    )

    first = service.start(_run("run-1"), candidate_workload_id="agent-1")
    assert first.cost_usd == pytest.approx(0.10)

    with pytest.raises(ShadowBudgetExceededError):
        service.start(_run("run-2"), candidate_workload_id="agent-1")
