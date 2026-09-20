"""Inconclusive verdict attribution.

Splits the undifferentiated ``inconclusive`` bucket by root cause so that
summaries can tell you whether to fix the model, the matcher, or the corpus:

- ``broad_trigger`` — trigger too generic to verify (model problem).
- ``matcher_gap`` — trigger specific but matcher recognized nothing (engine problem).
- ``corpus_mismatch`` — trajectory shape incompatible with the strategy,
  e.g. insufficient history (corpus/engine problem).
- ``ambiguous_evidence`` — conflicting or borderline signals (corpus problem).
"""

from __future__ import annotations

from typing import Literal

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import match_detail

InconclusiveReason = Literal[
    "broad_trigger", "matcher_gap", "corpus_mismatch", "ambiguous_evidence"
]

REASONS: tuple[InconclusiveReason, ...] = (
    "broad_trigger",
    "matcher_gap",
    "corpus_mismatch",
    "ambiguous_evidence",
)

# Triggers at or below this word count are too generic to verify.
_BROAD_TRIGGER_WORDS = 2

# Minimum trajectories for a trustworthy verdict (mirrors report.py).
_MIN_HISTORY = 3


def attribute_inconclusive(
    candidate: CandidateRule,
    trajectories: list[Trajectory],
    evidence: EvidenceReport,
) -> InconclusiveReason:
    """Return the root-cause category for an inconclusive *evidence* report."""
    if len(trajectories) < _MIN_HISTORY:
        return "corpus_mismatch"

    detail = match_detail(candidate, trajectories[0]) if trajectories else None
    content_words = int(detail["content_token_count"]) if detail else 0
    if 0 < content_words <= _BROAD_TRIGGER_WORDS:
        return "broad_trigger"

    if evidence.near_misses:
        return "ambiguous_evidence"

    if not evidence.failures_prevented and not evidence.successes_broken:
        return "matcher_gap"

    return "ambiguous_evidence"


def summarize_inconclusive(reports: list[EvidenceReport]) -> dict[str, int]:
    """Count inconclusive reports by root-cause reason.

    Returns a dict with all four reasons as keys (zero-filled).
    """
    summary: dict[str, int] = dict.fromkeys(REASONS, 0)
    for report in reports:
        if report.verdict == "inconclusive" and report.inconclusive_reason in summary:
            summary[str(report.inconclusive_reason)] += 1
    return summary
