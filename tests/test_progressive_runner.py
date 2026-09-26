"""Tests for the production shadow runner over the run service (M37-01)."""

from __future__ import annotations

from collections.abc import Callable

from hiveplane.core.workload import AgentWorkload
from hiveplane.progressive.models import ShadowStatus
from hiveplane.progressive.runner import RunShadowRunner
from test_execution_service import _service
from test_progressive_shadow import _run


def test_run_shadow_runner_mirrors_task_and_is_isolated(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    runs, _, _, _ = _service(make_manifest)
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
