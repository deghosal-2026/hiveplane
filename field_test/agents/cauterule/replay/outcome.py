"""Behavioral outcome signal (#720) — first slice.

Promotion currently gates on a *text* match (``match_score``). This module adds
a grounded *outcome* signal:

* a matched failure is **verified prevented** only when the rule is anchored to
  the specific failure (trigger grounding via
  :func:`~cauterule.replay.matcher.is_grounded` — a structured signature hit
  #725 or a distinctive error phrase) **and** the directive is addressed to the
  failure (:func:`~cauterule.replay.matcher.is_directive_grounded`, #762);
* a matched success is **verified broken** under the same grounding;
* anything else is **unverified**.

``match_precision``  = prevented / (prevented + broken)                    [text]
``outcome_precision`` = verified_prevented / (verified_prevented + verified_broken)

A true executor (apply the directive, observe the flipped outcome) is tracked
as follow-up work in #720.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import Verdict
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import is_directive_grounded, is_grounded
from cauterule.replay.scorer import compute_scores
from cauterule.replay.simulator import simulate


@dataclass(frozen=True)
class OutcomeReport:
    """Behavioral-outcome breakdown for a candidate."""

    verified_prevented: tuple[str, ...] = field(default_factory=tuple)
    verified_broken: tuple[str, ...] = field(default_factory=tuple)
    unverified: tuple[str, ...] = field(default_factory=tuple)
    precision: float = 0.0
    verdict: Verdict = "inconclusive"


def simulate_outcome(candidate: CandidateRule, trajectory: Trajectory) -> str:
    """Return the grounded outcome: prevented / broken / unverified / no_effect.

    Grounding requires both a trigger anchored to the failure
    (:func:`~cauterule.replay.matcher.is_grounded`) and a directive addressed to
    the failure (:func:`~cauterule.replay.matcher.is_directive_grounded`,
    #762 Phase 1). A trigger-only match with an unanchored directive is
    ``unverified`` rather than credited. Phase 2 (#720) adds a true executor.
    """
    outcome = simulate(candidate, trajectory)
    if outcome in ("prevented", "broken"):
        if not is_grounded(candidate, trajectory):
            return "unverified"
        if not is_directive_grounded(candidate, trajectory):
            return "unverified"
        return outcome
    return outcome


def build_outcome_report(
    candidate: CandidateRule, trajectories: list[Trajectory]
) -> OutcomeReport:
    """Compute the grounded outcome precision/verdict over *trajectories*."""
    prevented: list[str] = []
    broken: list[str] = []
    unverified: list[str] = []
    for traj in trajectories:
        outcome = simulate_outcome(candidate, traj)
        if outcome == "prevented":
            prevented.append(traj.id)
        elif outcome == "broken":
            broken.append(traj.id)
        elif outcome == "unverified":
            unverified.append(traj.id)

    total_failures = sum(1 for t in trajectories if not t.success)
    precision, _recall, verdict = compute_scores(
        prevented=len(prevented),
        broken=len(broken),
        total_failures=total_failures,
        total_successes=len(trajectories) - total_failures,
        near_misses=0,
    )
    return OutcomeReport(
        verified_prevented=tuple(prevented),
        verified_broken=tuple(broken),
        unverified=tuple(unverified),
        precision=precision,
        verdict=verdict,
    )
