"""Close-call detection."""

from __future__ import annotations

from cauterule.extraction.ranking import RankedCandidate


def is_close_call(ranked: list[RankedCandidate], threshold: float = 0.05) -> bool:
    """Return True if top 2 candidates are within *threshold* (5% default).

    Comparison is based on precision difference.

    Args:
        ranked: Ranked candidates (best first).
        threshold: Relative threshold for close call.
    """
    if len(ranked) < 2:
        return False
    top = ranked[0]
    second = ranked[1]
    # Use precision as primary metric; if equal, use recall/confidence.
    diff = abs(top.evidence.precision - second.evidence.precision)
    return diff <= threshold


def get_close_call_candidates(
    ranked: list[RankedCandidate], threshold: float = 0.05
) -> list[RankedCandidate]:
    """Return candidates held for human review if close call, else top only."""
    if is_close_call(ranked, threshold=threshold):
        return ranked[:2]
    return ranked[:1] if ranked else []
