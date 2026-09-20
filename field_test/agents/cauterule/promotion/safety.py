"""Safety-first promotion gate — block promotion when safety corpora fail."""

from __future__ import annotations

from cauterule.models.evidence import EvidenceReport

SAFETY_CORPORA: frozenset[str] = frozenset({"successes", "failures/negative", "nearmiss"})

# Thresholds for autonomous promotion (hard block if not met).
SUCCESS_PASS_LIMIT = 0  # successes: any pass is violation
FAILURE_NEG_PASS_LIMIT = 0  # failures/negative: any pass is violation
NEARMISS_PRECISION_THRESHOLD = 0.9


def check_safety(
    evidence: EvidenceReport,
    corpus_name: str | None = None,
) -> list[str]:
    """Return safety warnings for *evidence* on *corpus_name*.

    Args:
        evidence: Replay evidence report for the candidate.
        corpus_name: Source corpus name (e.g. successes, failures/negative).
            If None, no safety checks are applied.

    Returns:
        List of safety warning strings (empty = no safety violation).
    """
    if corpus_name is None:
        return []

    lower = corpus_name.lower()
    base = lower.split("/")[-1].strip()
    # Normalize corpus name for matching.
    is_success = base == "successes" or lower == "successes"
    is_failure_neg = base == "failures/negative" or "failures/negative" in lower
    is_nearmiss = base == "nearmiss" or lower == "nearmiss"

    warnings: list[str] = []

    if is_success:
        # Any successes_broken >0 or pass verdict is a violation on successes.
        if evidence.successes_broken:
            warnings.append(
                f"safety: {corpus_name} has {len(evidence.successes_broken)} successes broken"
            )
        if evidence.verdict == "pass":
            warnings.append(f"safety: {corpus_name} pass is a false positive — must be zero")

    if is_failure_neg and evidence.verdict == "pass":
        warnings.append(f"safety: {corpus_name} pass is a rejection failure — must be zero")

    if (
        is_nearmiss
        and evidence.verdict != "inconclusive"
        and evidence.precision < NEARMISS_PRECISION_THRESHOLD
    ):
        warnings.append(
            f"safety: {corpus_name} precision {evidence.precision:.2f} below threshold {NEARMISS_PRECISION_THRESHOLD}"
        )

    return warnings
