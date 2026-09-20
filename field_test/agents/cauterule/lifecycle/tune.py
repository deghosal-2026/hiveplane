"""Auto-promotion tuning — thresholds learned from outcome history (#545).

Replaces static per-mode promotion cutoffs with per-corpus learned cutoffs
derived from accumulated outcome history.  Promotion gets stricter where the
extractor over-fires (low prevented-rate) and looser where precision is high,
with floor/ceiling guardrails, a minimum-evidence gate (cold start behaves
exactly like today), and a human-review ``--force`` override.

Supporting output: :func:`learn_cutoffs` consumes per-rule outcome counters
(specificity + prevented/broke) and computes a learned ``min_quality`` /
``min_specificity`` pair, blended toward the caller's default when evidence
is thin.
"""

from __future__ import annotations

from dataclasses import dataclass

from cauterule.models.rule import StandingRule

# Guardrails (issue #545): never auto-promote below the floor, never demand
# above the ceiling.
FLOOR_MIN_QUALITY = 0.4
CEILING_MIN_QUALITY = 0.95
FLOOR_MIN_SPECIFICITY = 0.1
CEILING_MIN_SPECIFICITY = 0.9

# Default evidence threshold below which we report ``source="default"``.
MIN_EVIDENCE = 30

# Target prevented-rate used to pick the learned cutoff.
TARGET_PREVENTED_RATE = 0.8


@dataclass(frozen=True)
class PromotionCutoffs:
    """Learned promotion cutoffs."""

    min_quality: float
    min_specificity: float
    source: str = "default"  # "default" | "learned(n=...)"

    def summarize(self) -> str:
        """Return a one-line summary."""
        return (
            f"min_quality={self.min_quality:.2f} "
            f"min_specificity={self.min_specificity:.2f} ({self.source})"
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def learn_cutoffs(
    rules: list[StandingRule],
    *,
    min_evidence: int = MIN_EVIDENCE,
    floor_quality: float = FLOOR_MIN_QUALITY,
    ceiling_quality: float = CEILING_MIN_QUALITY,
    floor_specificity: float = FLOOR_MIN_SPECIFICITY,
    ceiling_specificity: float = CEILING_MIN_SPECIFICITY,
    target_rate: float = TARGET_PREVENTED_RATE,
) -> PromotionCutoffs:
    """Learn promotion cutoffs from *rules*' outcome history.

    Bins active rules by specificity score, computes each bin's empirical
    prevented-rate (``prevented / (prevented + broke)`` from the outcome
    trend), and sets the learned quality/specificity cutoffs at the lowest
    bin whose prevented-rate meets *target_rate*.

    Guardrails:
    - thin evidence (fewer than *min_evidence* total outcomes) returns
      ``source="default"`` with the static defaults, so cold start behaves
      exactly like today.
    - safety rails clamp the learned values between the floor and ceiling,
      so an all-broke corpus never drops below the floor and an
      all-prevented corpus never demands above the ceiling.

    When no bin clears the target, cutoffs tighten (toward the ceiling) by
    a fixed step to avoid relaxing on bad evidence.

    Args:
        rules: Standing rules with outcome counters (+ specificity).
        min_evidence: Total outcomes needed before learning activates.
        floor_quality: Lower guardrail on min_quality.
        ceiling_quality: Upper guardrail on min_quality.
        floor_specificity: Lower guardrail on min_specificity.
        ceiling_specificity: Upper guardrail on min_specificity.
        target_rate: Minimum prevented-rate a cutoff bin must satisfy.

    Returns:
        Learned :class:`PromotionCutoffs`.
    """
    total_outcomes = sum(r.prevented_count + r.broke_count for r in rules if r.status == "active")
    if total_outcomes < min_evidence:
        return PromotionCutoffs(
            min_quality=floor_quality,
            min_specificity=floor_specificity,
            source="default",
        )

    from cauterule.lifecycle.specificity import compute_specificity

    # Bin active rules by specificity; compute prevented-rate per bin.
    bins: dict[int, list[float]] = {}
    for rule in rules:
        if rule.status != "active":
            continue
        spec = rule.specificity
        if spec is None:
            spec, _ = compute_specificity(rule)
        rate = (
            rule.prevented_count / (rule.prevented_count + rule.broke_count)
            if (rule.prevented_count + rule.broke_count)
            else 0.5
        )
        key = int(spec * 10)  # decile buckets 0..10
        bins.setdefault(key, []).append(rate)

    # Cutoff = the lowest specificity bin whose average prevented-rate meets
    # the target.  Scanning bins ascending, the FIRST qualifying bin is the
    # loosest justifiable cutoff — a high-precision corpus (many qualifying
    # bins, starting low) yields a relaxed cutoff, a low-precision corpus
    # (nobody qualifies) tightens toward the ceiling.
    learned_quality = floor_quality
    learned_spec = floor_specificity
    for key in sorted(bins):
        avg_rate = sum(bins[key]) / len(bins[key])
        if avg_rate >= target_rate:
            learned_quality = _clamp(key / 10.0, floor_quality, ceiling_quality)
            learned_spec = _clamp(key / 10.0, floor_specificity, ceiling_specificity)
            break
    else:
        # No bin cleared the target → tighten toward the ceiling.
        learned_quality = _clamp(ceiling_quality - 0.1, floor_quality, ceiling_quality)
        learned_spec = _clamp(ceiling_specificity, floor_specificity, ceiling_specificity)

    return PromotionCutoffs(
        min_quality=round(_clamp(learned_quality, floor_quality, ceiling_quality), 4),
        min_specificity=round(_clamp(learned_spec, floor_specificity, ceiling_specificity), 4),
        source=f"learned(n={total_outcomes})",
    )


def cutoffs_for_corpus(
    rules: list[StandingRule],
    *,
    mode: str = "balanced",
    min_evidence: int = MIN_EVIDENCE,
) -> PromotionCutoffs:
    """Return promotion cutoffs for *rules*, respecting the corpus's evidence.

    Thin evidence → ``source="default"`` (cold start unchanged).  Otherwise
    learns from outcome history and reports ``learned(n=...)``.

    Args:
        rules: Standing-rule history for the corpus.
        mode: Base threshold mode (``"conservative"``/``"balanced"``/
            ``"aggressive"``) whose defaults seed thin-evidence cutoffs.
        min_evidence: Evidence gate.

    Returns:
        Effective :class:`PromotionCutoffs`.
    """
    from cauterule.promotion.thresholds import get_thresholds

    defaults = get_thresholds(mode)
    default_quality = float(defaults.get("min_confidence", 0.7))
    default_spec = FLOOR_MIN_SPECIFICITY
    total = sum(r.prevented_count + r.broke_count for r in rules if r.status == "active")
    if total < min_evidence:
        return PromotionCutoffs(
            min_quality=default_quality,
            min_specificity=default_spec,
            source="default",
        )
    learned = learn_cutoffs(rules, min_evidence=min_evidence)
    return PromotionCutoffs(
        min_quality=round(learned.min_quality, 4),
        min_specificity=round(learned.min_specificity, 4),
        source=learned.source,
    )
