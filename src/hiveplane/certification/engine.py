"""Certification threshold evaluation engine (M6, #22).

Turns a :class:`BenchmarkResult` into a :class:`Certification`: evaluate the
summary against the target context's thresholds and advance the status
deterministically. Evaluation is pure given the inputs and an injected clock.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import (
    BenchmarkResult,
    Certification,
    CertificationEvent,
    CertificationPolicy,
    CertificationStatus,
    EvalSummary,
    TargetContext,
    Thresholds,
    advance_status,
)


def passes_threshold(summary: EvalSummary, thresholds: Thresholds) -> bool:
    """Return True when a benchmark summary meets every threshold.

    Critical failures are a hard block: they fail even when the pass rate and
    latency are otherwise within bounds.
    """
    if summary.critical_failures > thresholds.max_critical_failures:
        return False
    if summary.pass_rate < thresholds.min_pass_rate:
        return False
    return summary.p95_latency_ms <= thresholds.max_p95_latency_ms


class CertificationEngine:
    """Evaluates benchmark results against a :class:`CertificationPolicy`."""

    def __init__(
        self,
        policy: CertificationPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def policy(self) -> CertificationPolicy:
        """Return the policy this engine evaluates against."""
        return self._policy

    def evaluate(
        self,
        result: BenchmarkResult,
        *,
        current_status: CertificationStatus,
        target_context: TargetContext,
        production_runs_survived: int = 0,
    ) -> Certification:
        """Evaluate a benchmark result and return the resulting certification.

        A passing staging result promotes ``uncertified`` to ``provisional``. A
        passing production result promotes ``provisional`` to ``certified`` once
        the workload has survived the required production runs; until then the
        status is deferred (unchanged). A failed re-certification quarantines a
        provisional or certified workload.
        """
        thresholds = (
            self._policy.production
            if target_context is TargetContext.PRODUCTION
            else self._policy.staging
        )
        summary = result.to_eval_summary()
        status = self._resolve_status(
            current_status,
            summary,
            thresholds,
            target_context,
            production_runs_survived,
        )
        return Certification(
            certification_id=self._certification_id(result, target_context),
            workload_id=result.workload_id,
            manifest_version=result.manifest_version,
            status=status,
            target_context=target_context,
            benchmark_run_id=result.benchmark_run_id,
            thresholds=thresholds,
            eval_summary=summary,
            timestamp=self._clock(),
        )

    @staticmethod
    def _resolve_status(
        current_status: CertificationStatus,
        summary: EvalSummary,
        thresholds: Thresholds,
        target_context: TargetContext,
        production_runs_survived: int,
    ) -> CertificationStatus:
        if not passes_threshold(summary, thresholds):
            return _try_transition(current_status, CertificationEvent.RECERT_FAIL)
        if target_context is TargetContext.PRODUCTION:
            if production_runs_survived < thresholds.min_production_runs_survived:
                return current_status
            return _try_transition(current_status, CertificationEvent.PRODUCTION_PASS)
        return _try_transition(current_status, CertificationEvent.STAGING_PASS)

    @staticmethod
    def _certification_id(result: BenchmarkResult, target_context: TargetContext) -> str:
        payload = "|".join(
            (
                result.workload_id,
                str(result.manifest_version),
                result.benchmark_run_id,
                target_context.value,
            )
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"cert-{digest}"


def _try_transition(
    current: CertificationStatus, event: CertificationEvent
) -> CertificationStatus:
    """Apply an event, leaving the status unchanged when it is not permitted."""
    try:
        return advance_status(current, event)
    except ValueError:
        return current
