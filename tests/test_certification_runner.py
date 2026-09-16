"""Tests for the deterministic benchmark runner (M5, #20)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.certification.corpus import parse_corpus
from hiveplane.certification.errors import UnsupportedCheckError
from hiveplane.certification.models import (
    BenchmarkCorpus,
    Environment,
    ExpectedOutcome,
)
from hiveplane.certification.runner import (
    BenchmarkRunner,
    TaskExecution,
    evaluate_check,
)

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)


def _corpus(tasks: list[dict[str, Any]]) -> BenchmarkCorpus:
    return parse_corpus({"id": "test-corpus", "version": 1, "tasks": tasks})


class FakeExecutor:
    """Returns canned task executions keyed by task id."""

    def __init__(self, outcomes: dict[str, TaskExecution]) -> None:
        self._outcomes = outcomes

    def execute(self, task: Any) -> TaskExecution:
        return self._outcomes[task.id]


def _runner(executor: FakeExecutor, corpus: BenchmarkCorpus) -> BenchmarkRunner:
    return BenchmarkRunner(
        executor,
        model_identity="gpt-4o-2024-08-06",
        benchmark_version="1.0.0",
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
    )


def test_exact_match_check_passes() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "classify",
                "check": {"type": "exact_match", "field": "severity", "value": "high"},
            }
        ]
    )
    passed, reason = evaluate_check(
        corpus.tasks[0].check, TaskExecution(output={"severity": "high"}, latency_ms=1)
    )

    assert passed is True
    assert reason is None


def test_exact_match_check_fails_with_reason() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "classify",
                "check": {"type": "exact_match", "field": "severity", "value": "high"},
            }
        ]
    )
    passed, reason = evaluate_check(
        corpus.tasks[0].check, TaskExecution(output={"severity": "low"}, latency_ms=1)
    )

    assert passed is False
    assert reason is not None and "severity" in reason


def test_action_audit_detects_forbidden_action() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "restart",
                "check": {
                    "type": "action_audit",
                    "required_actions": ["restart service-b"],
                    "forbidden_actions": ["restart service-a"],
                },
            }
        ]
    )
    passed, reason = evaluate_check(
        corpus.tasks[0].check,
        TaskExecution(actions=["restart service-a", "restart service-b"], latency_ms=1),
    )

    assert passed is False
    assert reason is not None and "forbidden" in reason


def test_action_audit_detects_missing_required_action() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "restart",
                "check": {"type": "action_audit", "required_actions": ["restart service-b"]},
            }
        ]
    )
    passed, reason = evaluate_check(
        corpus.tasks[0].check, TaskExecution(actions=[], latency_ms=1)
    )

    assert passed is False
    assert reason is not None and "missing" in reason


def test_unsupported_check_type_raises() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "rubric",
                "check": {"type": "rubric", "criteria": ["a"], "min_score": 1},
            }
        ]
    )

    with pytest.raises(UnsupportedCheckError, match="rubric"):
        evaluate_check(corpus.tasks[0].check, TaskExecution(latency_ms=1))


def test_runner_aggregates_task_results() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "a",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            },
            {
                "id": "t2",
                "name": "b",
                "check": {"type": "exact_match", "field": "x", "value": 1},
                "critical": True,
            },
            {
                "id": "t3",
                "name": "c",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            },
        ]
    )
    executor = FakeExecutor(
        {
            "t1": TaskExecution(output={"x": 1}, latency_ms=10, tokens=100, trace_id="tr-1"),
            "t2": TaskExecution(output={"x": 2}, latency_ms=20, tokens=200, trace_id="tr-2"),
            "t3": TaskExecution(output={"x": 1}, latency_ms=30, tokens=300, trace_id="tr-3"),
        }
    )

    result = _runner(executor, corpus).run(corpus, workload_id="repo-agent", manifest_version=5)

    assert result.corpus_id == "test-corpus"
    assert result.corpus_version == 1
    assert result.model_identity == "gpt-4o-2024-08-06"
    assert result.aggregate.total == 3
    assert result.aggregate.passed == 2
    assert result.aggregate.failed == 1
    assert result.aggregate.critical_failures == 1
    assert result.aggregate.pass_rate == pytest.approx(2 / 3)
    assert result.aggregate.p50_latency_ms == 20
    assert result.aggregate.total_tokens == 600
    assert result.tasks[1].status == "fail"
    assert result.tasks[1].critical is True


def test_runner_is_deterministic_for_same_inputs() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "a",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            }
        ]
    )
    executor = FakeExecutor({"t1": TaskExecution(output={"x": 1}, latency_ms=10)})
    runner = _runner(executor, corpus)

    first = runner.run(corpus, workload_id="repo-agent", manifest_version=5)
    second = runner.run(corpus, workload_id="repo-agent", manifest_version=5)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_runner_id_changes_with_manifest_version() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "a",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            }
        ]
    )
    executor = FakeExecutor({"t1": TaskExecution(output={"x": 1}, latency_ms=10)})
    runner = _runner(executor, corpus)

    v5 = runner.run(corpus, workload_id="repo-agent", manifest_version=5)
    v6 = runner.run(corpus, workload_id="repo-agent", manifest_version=6)

    assert v5.benchmark_run_id != v6.benchmark_run_id


def test_runner_eval_summary_matches_aggregate() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "a",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            },
            {
                "id": "t2",
                "name": "b",
                "check": {"type": "exact_match", "field": "x", "value": 1},
            },
        ]
    )
    executor = FakeExecutor(
        {
            "t1": TaskExecution(output={"x": 1}, latency_ms=10),
            "t2": TaskExecution(output={"x": 9}, latency_ms=20),
        }
    )

    summary = _runner(executor, corpus).run(
        corpus, workload_id="repo-agent", manifest_version=1
    ).to_eval_summary()

    assert summary.pass_rate == pytest.approx(0.5)
    assert summary.tasks_passed == 1
    assert summary.tasks_failed == 1
    assert summary.critical_failures == 0


def test_expected_outcome_is_available_on_task() -> None:
    corpus = _corpus(
        [
            {
                "id": "t1",
                "name": "a",
                "expected": {"outcome": "ok", "required_fields": ["summary"]},
                "check": {"type": "exact_match", "field": "x", "value": 1},
            }
        ]
    )

    assert corpus.tasks[0].expected == ExpectedOutcome(
        outcome="ok", required_fields=["summary"]
    )
