"""Mechanical corpus-coupling validation (M23 #123/#140, D20 spec).

Proves the benchmark is not theater: every corpus task must be checkable with
an implemented check type, reference only allowlisted tools in its required
actions, and discriminate — a stub *correct* agent passes everything while a
stub *broken* agent (constant low-risk labels, no flag actions) misses the
production threshold and, for repo-agent, fails a critical task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiveplane.certification.corpus import load_corpus
from hiveplane.certification.models import CheckStatus, CheckType, Environment
from hiveplane.certification.runner import BenchmarkRunner, TaskExecution
from hiveplane.core.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS_DIR = ROOT / "examples" / "workloads"
CORPORA_DIR = ROOT / "examples" / "corpora"
MODEL = "openai/gpt-4o/2024-08-06"
_PRODUCTION_THRESHOLD = 0.90

#: Model output the *broken* agent always produces: the label a naive model
#: would emit for any input, with no governance actions (D20 negative proof).
_BROKEN_OUTPUT: dict[str, str] = {
    "risk": "low",
    "severity": "info",
    "category": "tutorial",
    "draft": "a draft",
}
_BROKEN_ACTIONS: list[str] = []


def _environment(adapter: str) -> Environment:
    return Environment(
        sandbox_image="hiveplane/sandbox:0.1.0",
        runtime_adapter=adapter,
        control_plane_version="0.1.0",
    )


class _StubExecutor:
    """Executes tasks with a configurable output/actions policy."""

    def __init__(self, output: dict[str, Any], actions: list[str]) -> None:
        self._output = output
        self._actions = actions

    def execute(self, task: Any) -> TaskExecution:
        return TaskExecution(
            output=dict(self._output),
            actions=list(self._actions),
            latency_ms=1,
            model_identity=MODEL,
        )


def _correct_actions(task: Any) -> list[str]:
    """The actions a correct agent emits: the audit's required set."""
    if task.check.type is CheckType.ACTION_AUDIT:
        return list(task.check.required_actions)
    return []


def _corpus_dir(workload: str) -> Path:
    manifest = load_manifest(WORKLOADS_DIR / f"{workload}.yaml")
    certification = manifest.spec.certification
    assert certification is not None and certification.benchmark_corpus is not None
    return CORPORA_DIR / certification.benchmark_corpus.removeprefix("corpora/")


def _run_stub(workload: str, executor: Any) -> Any:
    manifest = load_manifest(WORKLOADS_DIR / f"{workload}.yaml")
    corpus = load_corpus(_corpus_dir(workload))
    runner = BenchmarkRunner(
        executor,
        model_identity=MODEL,
        benchmark_version="1.0.0",
        environment=_environment(manifest.spec.runtime.adapter.value),
    )
    return runner.run(corpus, workload_id=workload, manifest_version=1)


def test_every_corpus_uses_only_implemented_checks() -> None:
    for workload in ("repo-agent", "docs-agent", "incident-agent"):
        corpus = load_corpus(_corpus_dir(workload))
        for task in corpus.tasks:
            assert task.check.type in (CheckType.EXACT_MATCH, CheckType.ACTION_AUDIT), (
                f"{workload}/{task.id}: unimplemented check type {task.check.type}"
            )


def test_every_required_action_is_an_allowlisted_tool() -> None:
    for workload in ("repo-agent", "docs-agent", "incident-agent"):
        manifest = load_manifest(WORKLOADS_DIR / f"{workload}.yaml")
        allow = {entry.tool_id for entry in (manifest.spec.tools.allow or [])}
        corpus = load_corpus(_corpus_dir(workload))
        for task in corpus.tasks:
            if task.check.type is not CheckType.ACTION_AUDIT:
                continue
            for action in task.check.required_actions:
                assert action in allow, (
                    f"{workload}/{task.id}: required action {action!r} is not allowlisted"
                )


def test_every_corpus_has_a_critical_task() -> None:
    for workload in ("repo-agent", "docs-agent", "incident-agent"):
        corpus = load_corpus(_corpus_dir(workload))
        assert any(task.critical for task in corpus.tasks), f"{workload}: no critical task"


def test_correct_agent_passes_every_corpus() -> None:
    for workload in ("repo-agent", "docs-agent", "incident-agent"):
        corpus = load_corpus(_corpus_dir(workload))
        outputs: dict[str, Any] = {
            task.check.field or "": task.check.value
            for task in corpus.tasks
            if task.check.type is CheckType.EXACT_MATCH
        }
        result = _run_stub(workload, _PerTaskExecutor(outputs))
        assert result.aggregate.pass_rate == 1.0, f"{workload}: correct agent failed"
        assert result.aggregate.critical_failures == 0, f"{workload}: critical failure"


def test_broken_agent_misses_the_production_threshold() -> None:
    broken = _StubExecutor(_BROKEN_OUTPUT, _BROKEN_ACTIONS)
    for workload in ("repo-agent", "docs-agent", "incident-agent"):
        result = _run_stub(workload, broken)
        assert result.aggregate.pass_rate < _PRODUCTION_THRESHOLD, (
            f"{workload}: broken agent still passes at production threshold "
            f"({result.aggregate.pass_rate:.2f}) — benchmark is theater"
        )


def test_broken_agent_fails_a_critical_repo_task() -> None:
    broken = _StubExecutor(_BROKEN_OUTPUT, _BROKEN_ACTIONS)
    result = _run_stub("repo-agent", broken)

    failed = [task for task in result.tasks if task.status is CheckStatus.FAIL]
    assert any(task.critical for task in failed), "no critical task failed for the broken agent"


class _PerTaskExecutor:
    """Oracle-shaped executor: emits each task's declared passing output."""

    def __init__(self, outputs: dict[str, Any]) -> None:
        self._outputs = outputs

    def execute(self, task: Any) -> TaskExecution:
        output = dict(self._outputs)
        if task.check.type is CheckType.EXACT_MATCH:
            output[task.check.field] = task.check.value
        return TaskExecution(
            output=output,
            actions=_correct_actions(task),
            latency_ms=1,
            model_identity=MODEL,
        )
