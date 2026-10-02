"""Regression diff between two benchmark results (DD-11, M7; M33).

The diff pinpoints exactly which corpus tasks regressed, quantifies the metric
deltas, classifies severity, and attaches a replayable frame set to every
changed task. It is deterministic: identical results produce an empty diff.
"""

from __future__ import annotations

from datetime import datetime

from hiveplane.certification.models import (
    BenchmarkResult,
    BenchmarkTask,
    CheckStatus,
    RegressionDiff,
    RegressionReport,
    ReplayFrameSet,
    Severity,
    TaskDelta,
)


def _replay_frame(
    attestation_id: str,
    task_id: str,
    *,
    trace_id: str | None,
    task_catalog: dict[str, BenchmarkTask] | None,
) -> ReplayFrameSet:
    task = (task_catalog or {}).get(task_id)
    ref = f"certification:{attestation_id}/task:{task_id}"
    if task is None:
        return ReplayFrameSet(task_id=task_id, trace_id=trace_id, replay_ref=ref, replayable=False)
    return ReplayFrameSet(
        task_id=task_id,
        trace_id=trace_id,
        input=dict(task.input),
        expected=task.expected.model_dump(mode="json") if task.expected else {},
        check=task.check.model_dump(mode="json"),
        replay_ref=ref,
    )


def regression_diff(
    before: BenchmarkResult,
    after: BenchmarkResult,
    *,
    before_attestation_id: str,
    after_attestation_id: str,
    task_catalog: dict[str, BenchmarkTask] | None = None,
    baseline_attestation_id: str | None = None,
) -> RegressionDiff:
    """Compare two benchmark results task by task.

    A regression is a task that passed before and fails now; an improvement is
    the reverse. Any regression blocks promotion. A ``pass -> fail`` on a
    critical task is a critical regression.
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
            delta = TaskDelta(
                task_id=task_id,
                before=prior.status,
                after=current.status,
                failure_reason=current.failure_reason,
                critical=current.critical,
                severity=Severity.CRITICAL if current.critical else Severity.WARNING,
                latency_delta_ms=current.latency_ms - prior.latency_ms,
                tokens_delta=current.tokens - prior.tokens,
                cost_delta_usd=current.cost_usd - prior.cost_usd,
            )
            delta.replay = _replay_frame(
                after_attestation_id,
                task_id,
                trace_id=current.trace_id,
                task_catalog=task_catalog,
            )
            regressed.append(delta)
        elif prior.status is CheckStatus.FAIL and current.status is CheckStatus.PASS:
            delta = TaskDelta(
                task_id=task_id,
                before=prior.status,
                after=current.status,
                latency_delta_ms=current.latency_ms - prior.latency_ms,
                tokens_delta=current.tokens - prior.tokens,
                cost_delta_usd=current.cost_usd - prior.cost_usd,
            )
            delta.replay = _replay_frame(
                after_attestation_id,
                task_id,
                trace_id=current.trace_id,
                task_catalog=task_catalog,
            )
            improved.append(delta)

    critical_regressions = [delta.task_id for delta in regressed if delta.critical]
    warnings: list[str] = []
    removed_passes = [
        task_id for task_id in removed if before_by_id[task_id].status is CheckStatus.PASS
    ]
    if removed_passes:
        warnings.append(f"removed passing tasks: {', '.join(removed_passes)}")
    blocked = bool(regressed) or bool(removed_passes)
    severity = (
        Severity.CRITICAL
        if critical_regressions
        else Severity.WARNING
        if regressed
        else Severity.NONE
    )
    diff = RegressionDiff(
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
        blocked=blocked,
        severity=severity,
        critical_regressions=critical_regressions,
        warnings=warnings,
        baseline_attestation_id=baseline_attestation_id or before_attestation_id,
    )
    diff.summary = human_report(diff)
    return diff


def human_report(diff: RegressionDiff) -> str:
    """Render a concise human-readable summary of a regression diff."""
    if not diff.blocked and not diff.improved:
        return (
            f"No regressions across {diff.total} tasks "
            f"({diff.passed_after}/{diff.total} passing)."
        )
    lines = [
        f"{diff.severity.value.upper()}: {len(diff.regressed)} regression(s), "
        f"{len(diff.improved)} improvement(s) across {diff.total} tasks."
    ]
    for delta in diff.regressed:
        marker = "CRITICAL" if delta.critical else "warning"
        lines.append(
            f"  regressed [{marker}] {delta.task_id}: "
            f"latency {delta.latency_delta_ms:+d}ms, "
            f"tokens {delta.tokens_delta:+d}, cost ${delta.cost_delta_usd:+.4f}"
            + (f" ({delta.failure_reason})" if delta.failure_reason else "")
        )
    for warning in diff.warnings:
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


def build_regression_report(
    diff: RegressionDiff, *, generated_at: datetime
) -> RegressionReport:
    """Bundle a diff into a machine- and human-readable report (M33-05)."""
    return RegressionReport(
        workload_id=diff.workload_id,
        before_attestation_id=diff.before_attestation_id,
        after_attestation_id=diff.after_attestation_id,
        severity=diff.severity,
        blocked=diff.blocked,
        machine_report=diff.model_dump(mode="json"),
        human_report=diff.summary or human_report(diff),
        generated_at=generated_at,
    )
