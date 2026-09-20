"""Duplicate detector — semantically/lexically near-identical rules."""

from __future__ import annotations

from collections.abc import Callable

from cauterule.conflict.report import build_duplicate_report
from cauterule.models.conflict import ConflictReport
from cauterule.models.rule import StandingRule


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _tokens(s: str) -> set[str]:
    return set(_normalize(s).split())


def _combined_tokens(rule: StandingRule) -> set[str]:
    """Tokenize a rule's trigger + directive as one bag (for dedup)."""
    return _tokens(f"{rule.when.trigger} {rule.do.directive}")


def _jaccard(a: set[str], b: set[str]) -> float:
    """Return the Jaccard similarity ``|A & B| / |A or B|``.

    Jaccard (rather than the overlap coefficient) is used so a strictly
    subsuming pair — e.g. a generic fallback rule and a far more specific
    one — does not silently score 1.0 (code-review).
    """
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_contradiction(a: StandingRule, b: StandingRule) -> bool:
    """Return True if *a*/*b* are direct contradictions (#code-review).

    Same normalized trigger but materially different directives is a
    contradiction, not a duplicate — detected by :func:`detect_contradictions`.
    """
    if _normalize(a.when.trigger) != _normalize(b.when.trigger):
        return False
    trigger_tokens = _tokens(a.when.trigger)
    directive_overlap = 0.0
    if trigger_tokens:
        d_a = _tokens(a.do.directive)
        d_b = _tokens(b.do.directive)
        if d_a and d_b:
            directive_overlap = len(d_a & d_b) / min(len(d_a), len(d_b))
    return directive_overlap < 0.5


def detect_duplicates(
    rules: list[StandingRule],
    threshold: float = 0.7,
    similarity: Callable[[set[str], set[str]], float] = _jaccard,
) -> list[ConflictReport]:
    """Return ConflictReports for active rules that are near-duplicates.

    Pairwise over active rules. A pair is a duplicate when its *combined*
    normalized ``trigger + directive`` token bags are near-identical.
    Paraphrased but equivalent rules (e.g. ``"permission denied"`` vs
    ``"access denied"``) are surfaced as ``type="duplicate"`` reports,
    while direct contradictions (same trigger, different directive) are
    left to :func:`detect_contradictions`.

    Args:
        rules: The rule store.
        threshold: Jaccard similarity at or above which a pair is
            reported.
        similarity: Pluggable ``(a_tokens, b_tokens) -> float`` metric
            (defaults to Jaccard) so a future semantic matcher can be
            dropped in.

    Returns:
        A list of ``type="duplicate"`` reports.
    """
    reports: list[ConflictReport] = []
    active = [r for r in rules if r.status == "active"]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            if _is_contradiction(a, b):
                continue
            sim = similarity(_combined_tokens(a), _combined_tokens(b))
            if sim < threshold:
                continue
            reports.append(
                build_duplicate_report(
                    (a.id, b.id),
                    trigger=a.when.trigger,
                    resolution=(
                        f"Duplicate: similarity={sim:.2f} >= {threshold} "
                        f"('{a.when.trigger}' / '{b.when.trigger}')"
                    ),
                )
            )
    return reports
