"""Deterministic benchmark runner (M5, #20).

The runner executes each corpus task through an injected ``TaskExecutor``,
evaluates the task's deterministic check, and emits a structured, reproducible
:class:`BenchmarkResult`. Determinism comes from pinned inputs and model
identity, a content-addressed run id, and an injected clock.

The executor is the seam that runs a task inside a controlled environment
(sandbox, pinned model, no network unless the task allows it). Runtime adapters
implement it in Part 8; tests inject a fake.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.certification.errors import UnsupportedCheckError
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkCorpus,
    BenchmarkResult,
    BenchmarkTask,
    BenchmarkTaskCheck,
    BenchmarkTaskResult,
    CheckStatus,
    CheckType,
    Environment,
)


class TaskExecution(BaseModel):
    """The observable result of executing one benchmark task."""

    model_config = ConfigDict(extra="forbid")

    output: dict[str, JsonValue] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    tokens: int = Field(default=0, ge=0)
    trace_id: str | None = None


class TaskExecutor(Protocol):
    """Executes a single benchmark task inside a controlled environment."""

    def execute(self, task: BenchmarkTask) -> TaskExecution: ...


class ReferenceExecutor:
    """Replays each task's declared check as a passing execution.

    A deterministic stand-in for a runtime adapter (Part 8) used for local
    demos, CI, and corpus self-checks. It proves a corpus is satisfiable; it
    does not measure a real agent's quality.
    """

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        """Return the execution that the task's own check declares as passing."""
        if task.check.type is CheckType.EXACT_MATCH:
            return TaskExecution(
                output={task.check.field or "": task.check.value}, latency_ms=1
            )
        if task.check.type is CheckType.ACTION_AUDIT:
            return TaskExecution(actions=list(task.check.required_actions), latency_ms=1)
        raise UnsupportedCheckError(task.check.type)


def evaluate_check(
    check: BenchmarkTaskCheck, execution: TaskExecution
) -> tuple[bool, str | None]:
    """Evaluate a deterministic task check against an execution result.

    Returns:
        ``(passed, failure_reason)``; ``failure_reason`` is ``None`` on pass.

    Raises:
        UnsupportedCheckError: when the check type has no evaluator yet.
    """
    if check.type is CheckType.EXACT_MATCH:
        field = check.field or ""
        actual = execution.output.get(field)
        if actual == check.value:
            return True, None
        return False, f"exact_match: expected {field}={check.value!r}, got {actual!r}"
    if check.type is CheckType.ACTION_AUDIT:
        missing = [action for action in check.required_actions if action not in execution.actions]
        if missing:
            return False, f"action_audit: missing required actions {missing}"
        present = [action for action in check.forbidden_actions if action in execution.actions]
        if present:
            return False, f"action_audit: forbidden actions present {present}"
        return True, None
    raise UnsupportedCheckError(check.type)


class BenchmarkRunner:
    """Runs a corpus and produces a deterministic benchmark result."""

    def __init__(
        self,
        executor: TaskExecutor,
        *,
        model_identity: str,
        benchmark_version: str,
        environment: Environment,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        self._model_identity = model_identity
        self._benchmark_version = benchmark_version
        self._environment = environment
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self, corpus: BenchmarkCorpus, *, workload_id: str, manifest_version: int
    ) -> BenchmarkResult:
        """Execute every task in ``corpus`` and return the aggregate result."""
        started_at = self._clock()
        results: list[BenchmarkTaskResult] = []
        for task in corpus.tasks:
            execution = self._executor.execute(task)
            passed, reason = evaluate_check(task.check, execution)
            results.append(
                BenchmarkTaskResult(
                    task_id=task.id,
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    latency_ms=execution.latency_ms,
                    tokens=execution.tokens,
                    trace_id=execution.trace_id,
                    critical=task.critical,
                    failure_reason=reason,
                )
            )
        finished_at = self._clock()
        return BenchmarkResult(
            benchmark_run_id=self._run_id(workload_id, manifest_version, corpus),
            workload_id=workload_id,
            manifest_version=manifest_version,
            corpus_id=corpus.id,
            corpus_version=corpus.version,
            model_identity=self._model_identity,
            environment=self._environment,
            started_at=started_at,
            finished_at=finished_at,
            tasks=results,
            aggregate=_aggregate(results),
        )

    def _run_id(
        self, workload_id: str, manifest_version: int, corpus: BenchmarkCorpus
    ) -> str:
        payload = "|".join(
            (
                workload_id,
                str(manifest_version),
                corpus.id,
                str(corpus.version),
                self._model_identity,
                self._benchmark_version,
            )
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"br-{digest}"


def _aggregate(results: list[BenchmarkTaskResult]) -> BenchmarkAggregate:
    latencies = sorted(result.latency_ms for result in results)
    total = len(results)
    passed = sum(1 for result in results if result.status is CheckStatus.PASS)
    return BenchmarkAggregate(
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=(passed / total) if total else 0.0,
        critical_failures=sum(
            1 for result in results if result.status is CheckStatus.FAIL and result.critical
        ),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_tokens=sum(result.tokens for result in results),
    )


def _percentile(values: list[int], quantile: float) -> int:
    """Nearest-rank percentile; ``values`` must be sorted."""
    if not values:
        return 0
    rank = max(0, math.ceil(quantile * len(values)) - 1)
    return values[rank]
