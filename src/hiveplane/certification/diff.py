"""Regression diff between two benchmark results (DD-11, M7)."""

from __future__ import annotations

from hiveplane.certification.models import (
    BenchmarkResult,
    CheckStatus,
    RegressionDiff,
    TaskDelta,
)


def regression_diff(
    before: BenchmarkResult,
    after: BenchmarkResult,
    *,
    before_attestation_id: str,
    after_attestation_id: str,
) -> RegressionDiff:
    """Compare two benchmark results task by task.

    A regression is a task that passed before and fails now; an improvement is
    the reverse. Any regression blocks promotion.
    """
    before_by_id = {task.task_id: task for task in before.tasks}
    after_by_id = {task.task_id: task for task in after.tasks}
    common = sorted(set(before_by_id) & set(after_by_id))
    added = sorted(set(after_by_id) - set(before_by_id))
    removed = sorted(set(before_by_id) - set(after_by_id))

    regressed: list[TaskDelta] = []
    improved: list[TaskDelta] = []
    for task_id in common:
        prior = before_by_id[task_id]
        current = after_by_id[task_id]
        if prior.status is CheckStatus.PASS and current.status is CheckStatus.FAIL:
            regressed.append(
                TaskDelta(
                    task_id=task_id,
                    before=prior.status,
                    after=current.status,
                    failure_reason=current.failure_reason,
                )
            )
        elif prior.status is CheckStatus.FAIL and current.status is CheckStatus.PASS:
            improved.append(
                TaskDelta(task_id=task_id, before=prior.status, after=current.status)
            )

    return RegressionDiff(
        workload_id=after.workload_id,
        before_attestation_id=before_attestation_id,
        after_attestation_id=after_attestation_id,
        total=len(common),
        passed_before=sum(1 for task in before.tasks if task.status is CheckStatus.PASS),
        passed_after=sum(1 for task in after.tasks if task.status is CheckStatus.PASS),
        regressed=regressed,
        improved=improved,
        added=added,
        removed=removed,
        blocked=bool(regressed)
        or any(before_by_id[task_id].status is CheckStatus.PASS for task_id in removed),
    )
