"""Tests that the bundled example workloads validate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hiveplane.certification.corpus import load_corpus
from hiveplane.certification.models import CheckType, Environment
from hiveplane.certification.runner import BenchmarkRunner, TaskExecution
from hiveplane.core.manifest import load_manifest

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"
CORPORA_DIR = Path(__file__).resolve().parents[1] / "examples" / "corpora"
DEMO_CORPUS_DIR = CORPORA_DIR / "repo-agent" / "v1"

EXPECTED = {
    "repo-agent": "raw-worker",
    "docs-agent": "langgraph",
    "incident-agent": "raw-worker",
}


class _OracleExecutor:
    """Returns the outcome a task's own check declares as passing.

    This proves a corpus is well-formed and *satisfiable*; it is not an agent.
    """

    def execute(self, task: Any) -> TaskExecution:
        if task.check.type is CheckType.EXACT_MATCH:
            return TaskExecution(output={task.check.field: task.check.value}, latency_ms=1)
        if task.check.type is CheckType.ACTION_AUDIT:
            return TaskExecution(actions=list(task.check.required_actions), latency_ms=1)
        raise AssertionError(f"unsupported check type {task.check.type}")


@pytest.mark.parametrize(("name", "adapter"), sorted(EXPECTED.items()))
def test_example_workload_validates(name: str, adapter: str) -> None:
    workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")

    assert workload.name == name
    assert workload.spec.runtime.adapter.value == adapter
    assert workload.spec.budget.per_run_usd > 0
    assert workload.spec.tools is not None
    assert workload.certification_status.value == "uncertified"


def test_every_example_covers_the_required_blocks() -> None:
    for name in EXPECTED:
        workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")
        spec = workload.spec

        assert spec.runtime is not None, name
        assert spec.budget is not None, name
        assert spec.model.identity is not None, name
        assert spec.certification is not None, name
        assert spec.tools.allow or spec.tools.deny, name
        assert spec.approvals is not None, name
        assert spec.triggers, name
        assert spec.sandbox is not None, name
        assert spec.fan_out is not None, name


def test_demo_workload_links_to_the_seeded_corpus() -> None:
    workload = load_manifest(EXAMPLES_DIR / "repo-agent.yaml")

    assert workload.spec.certification is not None
    reference = workload.spec.certification.benchmark_corpus
    assert reference == "corpora/repo-agent/v1"
    assert (CORPORA_DIR / "repo-agent" / "v1" / "corpus.yaml").exists()


def test_demo_corpus_has_positive_and_counterexample_tasks() -> None:
    corpus = load_corpus(DEMO_CORPUS_DIR)

    positives = [task for task in corpus.tasks if task.check.type is CheckType.EXACT_MATCH]
    counterexamples = [
        task for task in corpus.tasks if task.check.type is CheckType.ACTION_AUDIT
    ]

    assert len(positives) >= 5
    assert len(counterexamples) >= 3
    assert any(task.critical for task in counterexamples)


def test_demo_corpus_is_satisfiable() -> None:
    corpus = load_corpus(DEMO_CORPUS_DIR)
    runner = BenchmarkRunner(
        _OracleExecutor(),
        model_identity="gpt-4o-2024-08-06",
        benchmark_version="1.0.0",
        environment=Environment(
            sandbox_image="hiveplane/sandbox:0.1.0",
            runtime_adapter="raw-worker",
            control_plane_version="0.1.0",
        ),
    )

    result = runner.run(corpus, workload_id="repo-agent", manifest_version=1)

    assert result.aggregate.passed == result.aggregate.total
    assert result.aggregate.critical_failures == 0
    assert result.aggregate.pass_rate == 1.0
