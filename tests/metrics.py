"""Metrics capture helpers for telemetry tests (M20, #50/#51)."""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


class RecordingMetrics:
    """A ``FleetMetrics`` spy that records every emission."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def called(self, method: str) -> list[dict[str, Any]]:
        """Return the kwargs of every call to ``method``."""
        return [kwargs for name, kwargs in self.calls if name == method]

    def record_run_state(self, *, workload: str, team: str | None, state: str) -> None:
        self._record("record_run_state", workload=workload, team=team, state=state)

    def record_run_duration(self, *, workload: str, team: str | None, seconds: float) -> None:
        self._record("record_run_duration", workload=workload, team=team, seconds=seconds)

    def record_failure(self, *, workload: str, team: str | None, reason: str) -> None:
        self._record("record_failure", workload=workload, team=team, reason=reason)

    def record_escalation(self, *, workload: str, team: str | None, rule: str) -> None:
        self._record("record_escalation", workload=workload, team=team, rule=rule)

    def record_intervention_latency(self, *, workload: str, seconds: float) -> None:
        self._record("record_intervention_latency", workload=workload, seconds=seconds)

    def record_tool_call(
        self,
        *,
        workload: str,
        team: str | None,
        tool_id: str,
        trust_level: str,
        outcome: str,
    ) -> None:
        self._record(
            "record_tool_call",
            workload=workload,
            team=team,
            tool_id=tool_id,
            trust_level=trust_level,
            outcome=outcome,
        )

    def record_policy_decision(
        self, *, workload: str, team: str | None, decision: str, rule: str
    ) -> None:
        self._record(
            "record_policy_decision",
            workload=workload,
            team=team,
            decision=decision,
            rule=rule,
        )

    def record_budget_burn(
        self, *, workload: str, team: str | None, run_id: str, cost_usd: float
    ) -> None:
        self._record(
            "record_budget_burn",
            workload=workload,
            team=team,
            run_id=run_id,
            cost_usd=cost_usd,
        )

    def record_spend(
        self, *, workload: str, team: str | None, model: str, cost_usd: float
    ) -> None:
        self._record(
            "record_spend", workload=workload, team=team, model=model, cost_usd=cost_usd
        )

    def record_budget_exceeded(
        self, *, workload: str, team: str | None, level: str
    ) -> None:
        self._record(
            "record_budget_exceeded", workload=workload, team=team, level=level
        )

    def record_certification(
        self, *, workload: str, team: str | None, status: str, duration_seconds: float
    ) -> None:
        self._record(
            "record_certification",
            workload=workload,
            team=team,
            status=status,
            duration_seconds=duration_seconds,
        )

    def record_attestation_verification(self, *, workload: str, result: str) -> None:
        self._record(
            "record_attestation_verification", workload=workload, result=result
        )

    def record_model_swap_block(self, *, workload: str) -> None:
        self._record("record_model_swap_block", workload=workload)

    def record_regression(self, *, workload: str) -> None:
        self._record("record_regression", workload=workload)


class MetricReader:
    """Collects metric data points from an OTel meter provider."""

    def __init__(self) -> None:
        self._reader = InMemoryMetricReader()
        self.provider = MeterProvider(metric_readers=[self._reader])
        self.meter = self.provider.get_meter("hiveplane-test")

    def points(self, name: str) -> list[tuple[dict[str, Any], float]]:
        """Return ``(attributes, value)`` for every data point of ``name``."""
        collected: list[tuple[dict[str, Any], float]] = []
        data = self._reader.get_metrics_data()
        if data is None:
            return collected
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        value = point.value if hasattr(point, "value") else point.sum
                        collected.append((dict(point.attributes or {}), value))
        return collected
