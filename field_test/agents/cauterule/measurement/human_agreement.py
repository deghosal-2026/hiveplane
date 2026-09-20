"""Human-vs-replay agreement measurement (#493; plan §5.5/§15.9).

After each sweep, sample candidates per replay-verdict bucket (pass / fail /
inconclusive) and have a reviewer independently rule on them. Agreement is the
fraction of sampled candidates where the human verdict matches replay. When
agreement drops below :data:`HUMAN_GATE_THRESHOLD` (0.8) the promotion gate
requires human approval (per plan §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass

HUMAN_GATE_THRESHOLD = 0.80
"""Below this agreement the promotion gate requires human approval."""


@dataclass(frozen=True)
class HumanAgreementReport:
    """Agreement summary and gate decision."""

    reviewed: int
    matches: int
    agreement: float
    below_gate: bool
    gate_threshold: float
    by_verdict: dict[str, int]


def agreement_rate(reviews: list[dict[str, object]]) -> float:
    """Fraction of *reviews* where ``human_verdict`` matches ``replay_verdict``."""
    if not reviews:
        return 0.0
    matches = sum(
        1 for review in reviews if review.get("human_verdict") == review.get("replay_verdict")
    )
    return matches / len(reviews)


def human_agreement_report(reviews: list[dict[str, object]]) -> HumanAgreementReport:
    """Build the agreement report + human-gate decision for *reviews*."""
    reviewed = len(reviews)
    matches = sum(
        1 for review in reviews if review.get("human_verdict") == review.get("replay_verdict")
    )
    rate = matches / reviewed if reviewed else 0.0
    by_verdict: dict[str, int] = {}
    for review in reviews:
        verdict = str(review.get("replay_verdict", "unknown"))
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    return HumanAgreementReport(
        reviewed=reviewed,
        matches=matches,
        agreement=rate,
        below_gate=bool(reviewed) and rate < HUMAN_GATE_THRESHOLD,
        gate_threshold=HUMAN_GATE_THRESHOLD,
        by_verdict=by_verdict,
    )


def sample_for_review(
    results: list[dict[str, object]],
    *,
    per_bucket: int = 5,
) -> list[dict[str, object]]:
    """Sample up to *per_bucket* completed candidates from each verdict bucket.

    Returns records shaped for reviewer annotation: ``trajectory_id``,
    ``bucket``, ``trigger``/``directive`` (when present), and empty
    ``human_verdict``/``reviewer`` fields to be filled in.
    """
    buckets: dict[str, int] = {}
    sampled: list[dict[str, object]] = []
    for result in results:
        best = result.get("best")
        if not isinstance(best, dict) or not best:
            continue
        verdict = str(best.get("verdict", "unknown"))
        if buckets.get(verdict, 0) >= per_bucket:
            continue
        buckets[verdict] = buckets.get(verdict, 0) + 1
        sampled.append(
            {
                "trajectory_id": result.get("trajectory_id"),
                "bucket": verdict,
                "replay_verdict": verdict,
                "trigger": best.get("when") or best.get("trigger"),
                "directive": best.get("do") or best.get("directive"),
                "human_verdict": "",
                "reviewer": "",
            }
        )
    return sampled
