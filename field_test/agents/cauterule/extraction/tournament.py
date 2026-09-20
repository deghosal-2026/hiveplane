"""Draft tournament runner."""

from __future__ import annotations

from typing import Any

from cauterule.extraction.quality import is_valid
from cauterule.extraction.ranking import RankedCandidate, rank_candidates
from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.report import build_evidence_report


def run_tournament(
    candidates: list[CandidateRule],
    trajectories: list[Trajectory],
    scorer: Any | None = None,
    source: Trajectory | None = None,
    confidence_threshold: float = 0.6,
) -> list[RankedCandidate]:
    """Replay-test all candidates and rank them.

    Args:
        candidates: Candidates from multi-pass.
        trajectories: Historical trajectories for replay.
        scorer: Optional custom scorer (unused, reserved for future integration).
        source: Source trajectory the candidates were extracted from. When
            given, candidates failing the quality gate never enter ranking (#497).
        confidence_threshold: Quality-gate threshold used with *source*.

    Returns:
        Ranked candidates best first.
    """
    _ = scorer
    if source is not None:
        candidates = [c for c in candidates if is_valid(c, source, confidence_threshold)]
    evidences = [build_evidence_report(c, trajectories) for c in candidates]
    # Override verdict: pass only if precision == 1.0 and at least one failure prevented
    for ev in evidences:
        if ev.precision == 1.0 and len(ev.failures_prevented) >= 1:
            object.__setattr__(ev, "verdict", "pass")
        else:
            object.__setattr__(ev, "verdict", "fail")
    return rank_candidates(candidates, evidences)
