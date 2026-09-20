"""Evidence report builder."""

from __future__ import annotations

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport
from cauterule.models.trajectory import Trajectory
from cauterule.replay.attribution import attribute_inconclusive
from cauterule.replay.outcome import build_outcome_report
from cauterule.replay.scorer import compute_scores_detailed
from cauterule.replay.simulator import simulate

# Minimum history for a trustworthy verdict (#521). With fewer trajectories
# the surfaced verdict is always "inconclusive" (override is transparent).
MIN_TRAJECTORIES = 3


def build_evidence_report(
    candidate: CandidateRule,
    trajectories: list[Trajectory],
    threshold: float | None = None,
) -> EvidenceReport:
    """Build an :class:`EvidenceReport` for *candidate* against *trajectories*.

    Args:
        candidate: The candidate rule.
        trajectories: Reference trajectories to test against.
        threshold: Matcher threshold. If None, uses DEFAULT_THRESHOLD (0.6).
            Use :func:`threshold_for_corpus` from matcher.py for corpus-aware tuning.
    """
    prevented: list[str] = []
    broken: list[str] = []
    near_misses: list[str] = []
    trace: list[dict[str, str]] = []

    total_failures = sum(1 for t in trajectories if not t.success)

    kwargs = {}
    if threshold is not None:
        kwargs["threshold"] = threshold

    for traj in trajectories:
        outcome = simulate(candidate, traj, **kwargs)
        trace.append({"trajectory_id": traj.id, "outcome": outcome})
        if outcome == "prevented":
            prevented.append(traj.id)
        elif outcome == "broken":
            broken.append(traj.id)
        elif outcome == "near_miss":
            near_misses.append(traj.id)

    precision, recall, computed_verdict, scorer_reason = compute_scores_detailed(
        prevented=len(prevented),
        broken=len(broken),
        total_failures=total_failures,
        total_successes=len(trajectories) - total_failures,
        near_misses=len(near_misses),
    )

    # Explicit min-sample policy: with too little history, the surfaced
    # verdict is "inconclusive" regardless of what the scorer computed.
    # The override is transparent (verdict_reason records the computed
    # verdict) instead of silently discarding it (#521).
    verdict = computed_verdict
    verdict_reason: str | None = None
    if len(trajectories) < MIN_TRAJECTORIES:
        verdict = "inconclusive"
        verdict_reason = (
            f"min_sample: {len(trajectories)} < {MIN_TRAJECTORIES}; computed={computed_verdict}"
        )
    elif computed_verdict in ("inconclusive", "fail"):
        # #724: surface the true blocking reason (blocked_by_broken /
        # blocked_by_near_miss / no_signal).
        verdict_reason = scorer_reason

    # #720: behavioral (grounded) outcome, reported alongside the text verdict.
    # The text verdict still gates promotion for now; outcome_precision lets the
    # next field test compare the two and validate the proxy before the gate
    # migrates to it.
    outcome_report = build_outcome_report(candidate, trajectories)

    report = EvidenceReport(
        failures_prevented=tuple(prevented),
        successes_broken=tuple(broken),
        near_misses=tuple(near_misses),
        precision=precision,
        recall=recall,
        verdict=verdict,
        replay_trace=tuple(trace),
        verdict_reason=verdict_reason,
        outcome_precision=outcome_report.precision,
        outcome_verdict=outcome_report.verdict,
    )
    if report.verdict == "inconclusive":
        # Attribution needs the report; reconstruct once with the reason set.
        reason = attribute_inconclusive(candidate, trajectories, report)
        report = EvidenceReport(
            failures_prevented=report.failures_prevented,
            successes_broken=report.successes_broken,
            near_misses=report.near_misses,
            precision=report.precision,
            recall=report.recall,
            verdict=report.verdict,
            replay_trace=report.replay_trace,
            inconclusive_reason=reason,
            verdict_reason=report.verdict_reason,
            outcome_precision=report.outcome_precision,
            outcome_verdict=report.outcome_verdict,
        )
    # OTEL replay.verdict span (#588): best-effort, never blocks replay.
    try:
        from cauterule.integrations.otel import OtelExporter

        OtelExporter().emit_replay_verdict(
            verdict=report.verdict,
            precision_score=report.precision,
            recall_score=report.recall,
        )
    except Exception:
        pass
    return report
