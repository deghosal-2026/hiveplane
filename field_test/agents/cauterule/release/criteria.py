"""Release criteria with enforced thresholds tied to safety corpora."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReleaseThresholds:
    """Thresholds for release gate (from issue #427)."""

    successes_pass_rate: float = 0.0
    failures_negative_pass_rate: float = 0.0
    nearmiss_precision: float = 0.9
    golden_pass_rate: float = 0.7
    failures_positive_pass_rate: float = 0.5
    inconclusive_curated_max: float = 0.15
    inconclusive_raw_max: float = 0.40


DEFAULT_THRESHOLDS = ReleaseThresholds()


@dataclass(frozen=True)
class ThresholdCheck:
    """Single threshold check result."""

    name: str
    passed: bool
    actual: float
    threshold: float
    message: str = ""


@dataclass(frozen=True)
class ReleaseVerdict:
    """Aggregate release gate verdict."""

    passed: bool
    checks: tuple[ThresholdCheck, ...] = field(default_factory=tuple)

    @property
    def failures(self) -> list[str]:
        """Return messages for failed checks."""
        return [c.message for c in self.checks if not c.passed]


def check_release_criteria(
    *,
    successes_pass_rate: float = 0.0,
    failures_negative_pass_rate: float = 0.0,
    nearmiss_precision: float = 1.0,
    golden_pass_rate: float = 1.0,
    failures_positive_pass_rate: float = 1.0,
    inconclusive_curated_rate: float = 0.0,
    inconclusive_raw_rate: float = 0.0,
    thresholds: ReleaseThresholds = DEFAULT_THRESHOLDS,
) -> ReleaseVerdict:
    """Check all release thresholds and return verdict.

    Args:
        successes_pass_rate: Pass rate on successes (must be 0).
        failures_negative_pass_rate: Pass rate on failures/negative (must be 0).
        nearmiss_precision: Precision on nearmiss (must be >=0.9).
        golden_pass_rate: Pass rate on golden (must be >=0.7).
        failures_positive_pass_rate: Pass rate on failures/positive (must be >=0.5).
        inconclusive_curated_rate: Inconclusive rate on curated (must be <0.15).
        inconclusive_raw_rate: Inconclusive rate on raw (must be <0.40).
        thresholds: Thresholds to check against.

    Returns:
        :class:`ReleaseVerdict` with PASS/FAIL.
    """
    checks: list[ThresholdCheck] = []

    checks.append(
        ThresholdCheck(
            name="successes_pass_rate",
            passed=successes_pass_rate <= thresholds.successes_pass_rate,
            actual=successes_pass_rate,
            threshold=thresholds.successes_pass_rate,
            message=f"successes pass rate {successes_pass_rate:.1%} > {thresholds.successes_pass_rate:.0%} — must be zero"  # noqa: E501
            if successes_pass_rate > thresholds.successes_pass_rate
            else f"successes pass rate {successes_pass_rate:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="failures_negative_pass_rate",
            passed=failures_negative_pass_rate <= thresholds.failures_negative_pass_rate,
            actual=failures_negative_pass_rate,
            threshold=thresholds.failures_negative_pass_rate,
            message=f"failures/negative pass rate {failures_negative_pass_rate:.1%} > {thresholds.failures_negative_pass_rate:.0%}"  # noqa: E501
            if failures_negative_pass_rate > thresholds.failures_negative_pass_rate
            else f"failures/negative pass rate {failures_negative_pass_rate:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="nearmiss_precision",
            passed=nearmiss_precision >= thresholds.nearmiss_precision,
            actual=nearmiss_precision,
            threshold=thresholds.nearmiss_precision,
            message=f"nearmiss precision {nearmiss_precision:.1%} < {thresholds.nearmiss_precision:.0%}"  # noqa: E501
            if nearmiss_precision < thresholds.nearmiss_precision
            else f"nearmiss precision {nearmiss_precision:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="golden_pass_rate",
            passed=golden_pass_rate >= thresholds.golden_pass_rate,
            actual=golden_pass_rate,
            threshold=thresholds.golden_pass_rate,
            message=f"golden pass rate {golden_pass_rate:.1%} < {thresholds.golden_pass_rate:.0%}"
            if golden_pass_rate < thresholds.golden_pass_rate
            else f"golden pass rate {golden_pass_rate:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="failures_positive_pass_rate",
            passed=failures_positive_pass_rate >= thresholds.failures_positive_pass_rate,
            actual=failures_positive_pass_rate,
            threshold=thresholds.failures_positive_pass_rate,
            message=f"failures/positive pass rate {failures_positive_pass_rate:.1%} < {thresholds.failures_positive_pass_rate:.0%}"  # noqa: E501
            if failures_positive_pass_rate < thresholds.failures_positive_pass_rate
            else f"failures/positive pass rate {failures_positive_pass_rate:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="inconclusive_curated",
            passed=inconclusive_curated_rate < thresholds.inconclusive_curated_max,
            actual=inconclusive_curated_rate,
            threshold=thresholds.inconclusive_curated_max,
            message=f"curated inconclusive {inconclusive_curated_rate:.1%} >= {thresholds.inconclusive_curated_max:.0%}"  # noqa: E501
            if inconclusive_curated_rate >= thresholds.inconclusive_curated_max
            else f"curated inconclusive {inconclusive_curated_rate:.1%} OK",
        )
    )
    checks.append(
        ThresholdCheck(
            name="inconclusive_raw",
            passed=inconclusive_raw_rate < thresholds.inconclusive_raw_max,
            actual=inconclusive_raw_rate,
            threshold=thresholds.inconclusive_raw_max,
            message=f"raw inconclusive {inconclusive_raw_rate:.1%} >= {thresholds.inconclusive_raw_max:.0%}"  # noqa: E501
            if inconclusive_raw_rate >= thresholds.inconclusive_raw_max
            else f"raw inconclusive {inconclusive_raw_rate:.1%} OK",
        )
    )

    passed = all(c.passed for c in checks)
    return ReleaseVerdict(passed=passed, checks=tuple(checks))
