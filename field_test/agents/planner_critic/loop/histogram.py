"""Family-histogram cycling detection (#217).

Completes the stall-signal matrix. Each existing signal is blind to the
cycling stall in its own way:

* **F-06 content convergence** — blocker text differs every revision;
* **F-07 regression** — LLM blockers are excluded by design;
* **#152 structural oscillation** — task ids/edges genuinely change between
  the two shapes;
* **#183 histogram stasis** — consecutive histograms are never *equal*.

The planner alternates repair strategies: revision N fixes family A and
introduces B; revision N+1 fixes B and re-breaks A. The blocker-family
histogram repeats at lag ≥ 2 while consecutive revisions differ. That repeat
is the reshuffling-vs-repairing distinction, made checkable:

    detect_histogram_cycling([h1, h2, h3, h4], max_lag=2)  # h4 == h2 → True

Histograms count **blocker-severity LLM findings per heuristic family**
(deterministic-gate findings carry no heuristic family and are excluded;
warnings are excluded). Constant histograms are deliberately *not* cycling —
identical consecutive revisions are stasis territory (#183 / F-06).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..types import Finding, Severity

#: Canonical empty/normalized form: sorted ((family, count), …) pairs.
FamilyHistogram = tuple[tuple[str, int], ...]


def compute_family_histogram(findings: list[Finding]) -> FamilyHistogram:
    """Canonical blocker-family histogram for one revision.

    Args:
        findings: All findings produced for the revision (gates + critic).

    Returns:
        Sorted ``((family, count), …)`` over blocker-severity LLM findings.
        Warnings and deterministic-gate findings (no heuristic family) are
        excluded. Pure function — identical input yields identical output.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        if finding.severity is not Severity.BLOCKER:
            continue
        if finding.heuristic_family is None:
            continue
        key = str(finding.heuristic_family)
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items()))


def detect_histogram_cycling(
    histograms: Sequence[FamilyHistogram],
    max_lag: int = 2,
) -> bool:
    """True when the final histogram repeats at some lag ≥ 2 within reach.

    A **progress guard** is built in (#229): when the newest revision reduced
    total blocker mass versus its predecessor, a lag-p repeat is legitimate
    bimodal alternation on an improving trajectory — analysis/code work types
    alternating defect families while the planner converges — and never
    fires. Flat-mass repeats still fire; that ambiguity (alternation with no
    measurable progress) stays fail-safe escalate-only by design.

    Args:
        histograms: Per-revision histograms in revision order (most recent
            last).
        max_lag: Largest backward lag inspected (2 detects period-2
            A→B→A→B; 3 also catches period-3 wheels).

    Returns:
        True when the newest histogram equals a histogram exactly ``p``
        revisions back (``2 ≤ p ≤ max_lag``), differs from the immediately
        previous one, and shows no mass improvement over that previous one.
        Fewer than ``max_lag + 1`` entries never fires.
    """
    if len(histograms) < max_lag + 1:
        return False
    latest = histograms[-1]
    previous = histograms[-2]
    if latest == previous:
        return False
    if sum(count for _, count in latest) < sum(count for _, count in previous):
        return False  # progress guard (#229): still repairing, not reshuffling
    # Lag p compares the newest entry against exactly p revisions back.
    return any(latest == histograms[-1 - p] for p in range(2, max_lag + 1))


__all__ = [
    "FamilyHistogram",
    "compute_family_histogram",
    "detect_histogram_cycling",
]
