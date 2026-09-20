"""Cross-session repeat-failure measurement (#663/#496; plan §5.2/§7.3).

Implements the v0.3.0 headline metric:

    repeat_failure_rate(sessions) = repeat_failures / total_failures
    reduction = 1 - (rate_intervention / rate_baseline)

A failure is a *repeat* when its ``failure_class`` has already been seen earlier
in the ordered session list. The protocol (§4.3) compares a no-intervention
baseline against an intervention run over the same sessions; the late-session
window (default sessions 4-5) is where accumulated rules are expected to bite.
"""

from __future__ import annotations

from dataclasses import dataclass

REDUCTION_TARGET = 0.50
"""Release-gate target for cross-session repeat-failure reduction (#445/#496)."""


@dataclass(frozen=True)
class CrossSessionReport:
    """Baseline vs intervention repeat-failure comparison."""

    baseline_failures: int
    baseline_repeats: int
    intervention_failures: int
    intervention_repeats: int
    baseline_repeat_rate: float
    intervention_repeat_rate: float
    reduction: float
    meets_target: bool


def _failures(records: list[dict[str, object]], sessions: set[int] | None) -> list[object]:
    out: list[object] = []
    for record in records:
        if record.get("success") is True:
            continue
        if record.get("failure_class") is None:
            continue
        session = record.get("session")
        if sessions is not None and (not isinstance(session, int) or session not in sessions):
            continue
        out.append(record.get("failure_class"))
    return out


def repeat_failure_rate(
    records: list[dict[str, object]],
    *,
    sessions: set[int] | None = None,
) -> float:
    """Fraction of failures whose ``failure_class`` recurred.

    Records are ordered by session (input order is preserved); a failure is a
    repeat when its class was seen in an earlier failure record.
    """
    failures = _failures(records, sessions)
    if not failures:
        return 0.0
    seen: set[object] = set()
    repeats = 0
    for failure_class in failures:
        if failure_class in seen:
            repeats += 1
        else:
            seen.add(failure_class)
    return repeats / len(failures)


def cross_session_delta(
    baseline: list[dict[str, object]],
    intervention: list[dict[str, object]],
    *,
    late_sessions: set[int] | None = None,
) -> CrossSessionReport:
    """Compare baseline vs intervention repeat-failure rates.

    *late_sessions* restricts the measurement window (default: all sessions).
    Returns an all-zero report when the baseline has no failures, so a run with
    no signal cannot claim a reduction.
    """
    window = late_sessions
    baseline_failures = _failures(baseline, window)
    intervention_failures = _failures(intervention, window)
    if not baseline_failures:
        return CrossSessionReport(
            baseline_failures=0,
            baseline_repeats=0,
            intervention_failures=len(intervention_failures),
            intervention_repeats=0,
            baseline_repeat_rate=0.0,
            intervention_repeat_rate=0.0,
            reduction=0.0,
            meets_target=False,
        )

    baseline_rate = repeat_failure_rate(baseline, sessions=window)
    intervention_rate = repeat_failure_rate(intervention, sessions=window)
    reduction = 1.0 - (intervention_rate / baseline_rate) if baseline_rate else 0.0
    return CrossSessionReport(
        baseline_failures=len(baseline_failures),
        baseline_repeats=round(baseline_rate * len(baseline_failures)),
        intervention_failures=len(intervention_failures),
        intervention_repeats=round(intervention_rate * len(intervention_failures)),
        baseline_repeat_rate=baseline_rate,
        intervention_repeat_rate=intervention_rate,
        reduction=reduction,
        meets_target=reduction >= REDUCTION_TARGET,
    )
