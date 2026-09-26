"""Drift detection: compare current performance to the certified baseline (M34-02).

The detector is pure given its inputs, an injected clock, and an explicit
consecutive-failure count. It applies threshold and trend logic plus
false-positive controls (M34-08): a single flaky evaluation never quarantines.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import EvalSummary, Severity
from hiveplane.drift.models import DriftAssessment, DriftVerdict
from hiveplane.tenancy import DEFAULT_TENANT_ID


class DriftDetector:
    """Evaluates an :class:`EvalSummary` against a certified baseline summary."""

    def __init__(
        self,
        *,
        threshold_pass_rate: float = 0.10,
        max_new_failures: int = 2,
        required_consecutive_failures: int = 2,
        strong_multiplier: float = 2.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0.0 <= threshold_pass_rate <= 1.0:
            raise ValueError("threshold_pass_rate must be within [0, 1]")
        if max_new_failures < 0:
            raise ValueError("max_new_failures must be >= 0")
        if required_consecutive_failures < 1:
            raise ValueError("required_consecutive_failures must be >= 1")
        if strong_multiplier < 1.0:
            raise ValueError("strong_multiplier must be >= 1")
        self._threshold_pass_rate = threshold_pass_rate
        self._max_new_failures = max_new_failures
        self._required_consecutive_failures = required_consecutive_failures
        self._strong_multiplier = strong_multiplier
        self._clock = clock or (lambda: datetime.now(UTC))

    def assess(
        self,
        *,
        workload: str,
        baseline: EvalSummary,
        current: EvalSummary,
        baseline_attestation_id: str | None = None,
        consecutive_failures: int = 1,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> DriftAssessment:
        """Assess drift of ``current`` against ``baseline``.

        ``consecutive_failures`` is the number of consecutive exceeding
        evaluations including this one; it drives the false-positive control.
        """
        delta = round(baseline.pass_rate - current.pass_rate, 6)
        new_failures = max(0, current.tasks_failed - baseline.tasks_failed)
        critical_failures = current.critical_failures
        exceeded = (
            delta > self._threshold_pass_rate
            or new_failures > self._max_new_failures
        )
        strong = (
            critical_failures > 0
            or delta >= self._threshold_pass_rate * self._strong_multiplier
        )
        should_quarantine = exceeded and (
            consecutive_failures >= self._required_consecutive_failures or strong
        )
        if not exceeded:
            verdict = DriftVerdict.STABLE
        elif should_quarantine:
            verdict = DriftVerdict.DRIFTED
        else:
            verdict = DriftVerdict.WARNING
        reason = self._reason(
            verdict=verdict,
            delta=delta,
            new_failures=new_failures,
            critical_failures=critical_failures,
            consecutive_failures=consecutive_failures,
        )
        return DriftAssessment(
            workload=workload,
            baseline_attestation_id=baseline_attestation_id,
            verdict=verdict,
            pass_rate_before=baseline.pass_rate,
            pass_rate_after=current.pass_rate,
            pass_rate_delta=delta,
            failed_before=baseline.tasks_failed,
            failed_after=current.tasks_failed,
            new_failures=new_failures,
            critical_failures=critical_failures,
            threshold_pass_rate=self._threshold_pass_rate,
            max_new_failures=self._max_new_failures,
            required_consecutive_failures=self._required_consecutive_failures,
            consecutive_failures=consecutive_failures,
            exceeded=exceeded,
            strong_signal=strong,
            should_quarantine=should_quarantine,
            reason=reason,
            evidence={
                "pass_rate_delta": delta,
                "new_failures": new_failures,
                "critical_failures": critical_failures,
                "consecutive_failures": consecutive_failures,
                "threshold_pass_rate": self._threshold_pass_rate,
                "max_new_failures": self._max_new_failures,
            },
            tenant_id=tenant_id,
            timestamp=self._clock(),
        )

    @property
    def severity_for(self) -> Callable[[DriftAssessment], Severity]:
        """Return a function mapping an assessment to a quarantine severity."""
        return lambda assessment: (
            Severity.CRITICAL
            if assessment.critical_failures > 0 or assessment.strong_signal
            else Severity.WARNING
        )

    def _reason(
        self,
        *,
        verdict: DriftVerdict,
        delta: float,
        new_failures: int,
        critical_failures: int,
        consecutive_failures: int,
    ) -> str:
        if verdict is DriftVerdict.STABLE:
            return (
                f"performance within tolerance (pass-rate delta {delta:.3f}, "
                f"new failures {new_failures})"
            )
        detail = (
            f"pass-rate drop {delta:.3f} (threshold {self._threshold_pass_rate:.3f}), "
            f"{new_failures} new failure(s), {critical_failures} critical"
        )
        if verdict is DriftVerdict.WARNING:
            return (
                f"drift threshold exceeded but not yet confirmed "
                f"({consecutive_failures}/{self._required_consecutive_failures}): {detail}"
            )
        return (
            f"behavioral drift confirmed "
            f"({consecutive_failures}/{self._required_consecutive_failures}): {detail}"
        )
