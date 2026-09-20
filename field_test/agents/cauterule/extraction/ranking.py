"""Tournament ranking."""

from __future__ import annotations

from dataclasses import dataclass

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport


@dataclass(frozen=True)
class RankedCandidate:
    """Candidate with its evidence and rank."""

    candidate: CandidateRule
    evidence: EvidenceReport
    rank: int


# #731: minimum recall for a candidate to be selectable. Below this a
# precision-1.0 rule that prevents almost nothing (e.g. unsafe-004:
# precision 1.0, recall 0.096) is never preferred.
R_MIN_RECALL = 0.10


def score_candidate(precision: float, recall: float) -> float:
    """Shared selection objective (#731): recall-weighted F1 with a recall floor.

    Used by both production ranking and the field-test runner so they agree on
    which candidate wins. A candidate below ``R_MIN_RECALL`` scores 0.0.
    """
    if recall < R_MIN_RECALL:
        return 0.0
    denom = precision + recall
    return (2 * precision * recall / denom) if denom else 0.0


def rank_candidates(
    candidates: list[CandidateRule],
    evidences: list[EvidenceReport],
) -> list[RankedCandidate]:
    """Rank candidates by the shared objective, then confidence, then specificity.

    Args:
        candidates: List of candidates.
        evidences: Parallel list of evidence reports (same order).

    Returns:
        Ranked list sorted best first, with rank 1..N.
    """
    if len(candidates) != len(evidences):
        raise ValueError("candidates and evidences must be same length")

    paired = list(zip(candidates, evidences, strict=True))

    def sort_key(pair: tuple[CandidateRule, EvidenceReport]) -> tuple[float, float, int]:
        cand, ev = pair
        # Specificity proxy: length of trigger + context
        specificity = len(cand.when.trigger) + sum(len(c) for c in cand.when.context)
        return (score_candidate(ev.precision, ev.recall), cand.confidence, specificity)

    paired.sort(key=sort_key, reverse=True)

    return [
        RankedCandidate(candidate=cand, evidence=ev, rank=idx)
        for idx, (cand, ev) in enumerate(paired, start=1)
    ]
