"""Candidate deduplication."""

from __future__ import annotations

from cauterule.models.candidate import CandidateRule


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def is_duplicate(a: CandidateRule, b: CandidateRule) -> bool:
    """Return True if *a* and *b* are semantically identical."""
    return (
        _normalize(a.when.trigger) == _normalize(b.when.trigger)
        and _normalize(a.do.directive) == _normalize(b.do.directive)
        and _normalize(" ".join(a.when.context)) == _normalize(" ".join(b.when.context))
        # #774: different error_signatures describe different failure shapes;
        # merging them loses signature-hit grounding for one shape.
        and _normalize(a.when.signature or "") == _normalize(b.when.signature or "")
    )


def deduplicate(candidates: list[CandidateRule]) -> list[CandidateRule]:
    """Remove semantically identical candidates, keeping the best occurrence.

    When duplicates differ in confidence (e.g. one per extraction pass), the
    higher-confidence candidate is kept while preserving first-seen order.
    """
    seen: list[CandidateRule] = []
    for cand in candidates:
        dup_idx = next(
            (i for i, existing in enumerate(seen) if is_duplicate(cand, existing)),
            None,
        )
        if dup_idx is None:
            seen.append(cand)
        elif cand.confidence > seen[dup_idx].confidence:
            seen[dup_idx] = cand
    return seen
