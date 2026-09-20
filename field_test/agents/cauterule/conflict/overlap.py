"""Overlap detector — non-contradictory overlapping triggers."""

from __future__ import annotations

from cauterule.models.conflict import ConflictReport
from cauterule.models.rule import StandingRule

# Minimum fraction of trigger-token overlap required to report (#609).
# Without a floor, a single shared high-frequency word ("push") would flag
# unrelated rules as overlapping.
_MIN_OVERLAP_FRACTION = 0.5

# High-frequency English words that carry no trigger signal. Removed before
# computing token overlap so stop-word-only matches never report (#609).
_STOP_WORDS = frozenset(
    {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with", "it", "is", "be", "at"}
)


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _tokenize(s: str) -> set[str]:
    return {w for w in _normalize(s).split() if w not in _STOP_WORDS}


def _overlap_fraction(a_tokens: set[str], b_tokens: set[str]) -> float:
    """Return the Jaccard similarity ``|common| / |union|`` (#609).

    Jaccard (rather than overlap-coefficient) is used so a pair sharing
    only one generic token in a larger union — e.g. ``"git push to
    remote"`` vs ``"push to branch"`` — scores 0.25 and is rejected,
    while genuinely-overlapping triggers score >= 0.5.
    """
    if not a_tokens or not b_tokens:
        return 0.0
    common = a_tokens & b_tokens
    return len(common) / len(a_tokens | b_tokens)


def detect_overlaps(rules: list[StandingRule]) -> list[ConflictReport]:
    """Return ConflictReports for active rules with overlapping trigger keywords that are not direct contradictions."""  # noqa: E501
    reports: list[ConflictReport] = []
    active = [r for r in rules if r.status == "active"]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            a_tokens = _tokenize(a.when.trigger)
            b_tokens = _tokenize(b.when.trigger)
            common = a_tokens & b_tokens
            if not common:
                continue
            if _normalize(a.when.trigger) == _normalize(b.when.trigger):
                continue
            if _normalize(a.do.directive) == _normalize(b.do.directive):
                continue
            fraction = _overlap_fraction(a_tokens, b_tokens)
            if fraction < _MIN_OVERLAP_FRACTION:
                continue
            reports.append(
                ConflictReport(
                    type="overlap",
                    rules=(a.id, b.id),
                    trigger=a.when.trigger,
                    resolution=f"Rules '{a.id}' and '{b.id}' share trigger keywords "
                    f"({', '.join(sorted(common))}) but differ in directives: "
                    f"'{a.do.directive}' vs '{b.do.directive}' "
                    f"(overlap={fraction:.2f})",
                )
            )
    return reports
