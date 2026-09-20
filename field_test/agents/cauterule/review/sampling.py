"""Human review sampling workflow — replay vs human agreement."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport

DEFAULT_SAMPLE_N = 5
DEFAULT_AGREEMENT_THRESHOLD = 0.8


@dataclass(frozen=True)
class HumanScore:
    """Human review score for a single candidate."""

    candidate_id: str
    replay_verdict: str
    human_verdict: str
    trigger_specific: bool = True
    directive_actionable: bool = True
    safe_to_promote: bool = True

    @property
    def matches(self) -> bool:
        """Return True if human verdict matches replay verdict."""
        return self.replay_verdict == self.human_verdict


@dataclass(frozen=True)
class ReviewSample:
    """Sampled candidates for human review."""

    sampled: tuple[tuple[CandidateRule, EvidenceReport], ...] = field(default_factory=tuple)
    by_verdict: dict[str, int] = field(default_factory=dict)


def sample_candidates(
    candidates: list[tuple[CandidateRule, EvidenceReport]],
    n_per_bucket: int = DEFAULT_SAMPLE_N,
) -> ReviewSample:
    """Sample N candidates per verdict bucket deterministically.

    Args:
        candidates: List of (candidate, evidence) pairs.
        n_per_bucket: Number to sample per verdict (pass, inconclusive, fail).

    Returns:
        :class:`ReviewSample` with sampled pairs and counts by verdict.
    """
    buckets: dict[str, list[tuple[CandidateRule, EvidenceReport]]] = {
        "pass": [],
        "inconclusive": [],
        "fail": [],
    }
    for cand, ev in candidates:
        bucket = ev.verdict if ev.verdict in buckets else "fail"
        buckets[bucket].append((cand, ev))

    sampled: list[tuple[CandidateRule, EvidenceReport]] = []
    counts: dict[str, int] = {}
    for verdict, bucket in buckets.items():  # type: ignore[assignment]
        # Deterministic: sort by trigger text hash, take first N
        bucket_sorted = sorted(
            bucket, key=lambda x: hashlib.md5(x[0].when.trigger.encode()).hexdigest()
        )
        taken = bucket_sorted[:n_per_bucket]
        sampled.extend(taken)  # type: ignore[arg-type]
        counts[verdict] = len(taken)

    return ReviewSample(sampled=tuple(sampled), by_verdict=counts)


def agreement_rate(scores: list[HumanScore]) -> float:
    """Calculate replay vs human agreement rate."""
    if not scores:
        return 0.0
    matches = sum(1 for s in scores if s.matches)
    return round(matches / len(scores), 4)


def requires_human_approval(
    rate: float,
    threshold: float = DEFAULT_AGREEMENT_THRESHOLD,
) -> bool:
    """Return True if human approval is required (agreement below threshold)."""
    return rate < threshold
