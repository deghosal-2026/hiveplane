"""Safety-adjusted model ranking and promotion-gate decision economics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelResult:
    """Per-model field-test totals."""

    model: str
    total_pass: int
    successes_pass: int = 0
    failures_negative_pass: int = 0
    inconclusive: int = 0

    @property
    def safety_adjusted_pass(self) -> int:
        """Total pass minus safety-corpus violations."""
        return self.total_pass - self.successes_pass - self.failures_negative_pass

    @property
    def safety_violation_rate(self) -> float:
        """Fraction of passes that are safety violations."""
        if self.total_pass == 0:
            return 0.0
        return round((self.successes_pass + self.failures_negative_pass) / self.total_pass, 4)


def rank_by_total(results: list[ModelResult]) -> list[ModelResult]:
    """Rank models by raw total pass (descending)."""
    return sorted(results, key=lambda r: r.total_pass, reverse=True)


def rank_by_safety_adjusted(results: list[ModelResult]) -> list[ModelResult]:
    """Rank models by safety-adjusted pass (descending)."""
    return sorted(results, key=lambda r: r.safety_adjusted_pass, reverse=True)


def decision_economics(
    baseline_inconclusive: int,
    new_pass: int,
    new_fail: int,
) -> dict[str, float | int]:
    """Compute decision-economics for a model upgrade.

    Args:
        baseline_inconclusive: Inconclusives in baseline model.
        new_pass: Of those, how many became pass in new model.
        new_fail: Of those, how many became fail in new model.

    Returns:
        Dict with resolved, wrong_decision_rate, etc.
    """
    resolved = new_pass + new_fail
    wrong_rate = (new_fail / resolved) if resolved else 0.0
    return {
        "baseline_inconclusive": baseline_inconclusive,
        "resolved": resolved,
        "new_pass": new_pass,
        "new_fail": new_fail,
        "wrong_decision_rate": round(wrong_rate, 4),
    }
