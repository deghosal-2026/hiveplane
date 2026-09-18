"""Fleet, certification, and cost metrics (M20, #50/#51; DD-06).

Services emit through :func:`get_metrics`. The default sink is a no-op; the
application installs an OTLP-backed sink in its lifespan, so tests and library
use stay side-effect free. Metric names and labels follow
``docs/design/telemetry-design.md``.
"""

from __future__ import annotations

from typing import Protocol

from opentelemetry.metrics import Meter
from opentelemetry.util.types import AttributeValue


class FleetMetrics(Protocol):
    """Sink for fleet, certification, and cost signals."""

    def record_run_state(self, *, workload: str, team: str | None, state: str) -> None: ...

    def record_run_duration(
        self, *, workload: str, team: str | None, seconds: float
    ) -> None: ...

    def record_failure(self, *, workload: str, team: str | None, reason: str) -> None: ...

    def record_escalation(self, *, workload: str, team: str | None, rule: str) -> None: ...

    def record_intervention_latency(self, *, workload: str, seconds: float) -> None: ...

    def record_tool_call(
        self,
        *,
        workload: str,
        team: str | None,
        tool_id: str,
        trust_level: str,
        outcome: str,
    ) -> None: ...

    def record_policy_decision(
        self, *, workload: str, team: str | None, decision: str, rule: str
    ) -> None: ...

    def record_budget_burn(
        self, *, workload: str, team: str | None, run_id: str, cost_usd: float
    ) -> None: ...

    def record_spend(
        self, *, workload: str, team: str | None, model: str, cost_usd: float
    ) -> None: ...

    def record_budget_exceeded(
        self, *, workload: str, team: str | None, level: str
    ) -> None: ...

    def record_certification(
        self, *, workload: str, team: str | None, status: str, duration_seconds: float
    ) -> None: ...

    def record_attestation_verification(self, *, workload: str, result: str) -> None: ...

    def record_model_swap_block(self, *, workload: str) -> None: ...

    def record_regression(self, *, workload: str) -> None: ...


class NullFleetMetrics:
    """A metrics sink that records nothing."""

    def record_run_state(self, *, workload: str, team: str | None, state: str) -> None:
        """Discard a run-state signal."""

    def record_run_duration(
        self, *, workload: str, team: str | None, seconds: float
    ) -> None:
        """Discard a run-duration signal."""

    def record_failure(self, *, workload: str, team: str | None, reason: str) -> None:
        """Discard a failure signal."""

    def record_escalation(self, *, workload: str, team: str | None, rule: str) -> None:
        """Discard an escalation signal."""

    def record_intervention_latency(self, *, workload: str, seconds: float) -> None:
        """Discard an intervention-latency signal."""

    def record_tool_call(
        self,
        *,
        workload: str,
        team: str | None,
        tool_id: str,
        trust_level: str,
        outcome: str,
    ) -> None:
        """Discard a tool-call signal."""

    def record_policy_decision(
        self, *, workload: str, team: str | None, decision: str, rule: str
    ) -> None:
        """Discard a policy-decision signal."""

    def record_budget_burn(
        self, *, workload: str, team: str | None, run_id: str, cost_usd: float
    ) -> None:
        """Discard a budget-burn signal."""

    def record_spend(
        self, *, workload: str, team: str | None, model: str, cost_usd: float
    ) -> None:
        """Discard a spend signal."""

    def record_budget_exceeded(
        self, *, workload: str, team: str | None, level: str
    ) -> None:
        """Discard an over-budget signal."""

    def record_certification(
        self, *, workload: str, team: str | None, status: str, duration_seconds: float
    ) -> None:
        """Discard a certification signal."""

    def record_attestation_verification(self, *, workload: str, result: str) -> None:
        """Discard an attestation-verification signal."""

    def record_model_swap_block(self, *, workload: str) -> None:
        """Discard a model-swap-block signal."""

    def record_regression(self, *, workload: str) -> None:
        """Discard a regression signal."""


class OtelFleetMetrics:
    """A ``FleetMetrics`` sink backed by an OpenTelemetry meter."""

    def __init__(self, meter: Meter) -> None:
        self._runs = meter.create_counter(
            "hiveplane_runs_total", unit="{run}", description="Runs by state."
        )
        self._run_duration = meter.create_histogram(
            "hiveplane_run_duration_seconds", unit="s", description="Run duration."
        )
        self._failures = meter.create_counter(
            "hiveplane_failures_total", unit="{failure}", description="Failures by reason."
        )
        self._escalations = meter.create_counter(
            "hiveplane_escalations_total", unit="{escalation}", description="Escalations."
        )
        self._intervention_latency = meter.create_histogram(
            "hiveplane_intervention_latency_seconds",
            unit="s",
            description="Time from a run's last state change to an operator action.",
        )
        self._tool_calls = meter.create_counter(
            "hiveplane_tool_calls_total", unit="{call}", description="Tool calls by outcome."
        )
        self._policy_decisions = meter.create_counter(
            "hiveplane_policy_decisions_total",
            unit="{decision}",
            description="Policy decisions by outcome.",
        )
        self._budget_burn = meter.create_gauge(
            "hiveplane_budget_burn_usd", unit="USD", description="Cumulative run cost."
        )
        self._spend = meter.create_counter(
            "hiveplane_spend_usd_total", unit="USD", description="Attributed spend."
        )
        self._budget_exceeded = meter.create_counter(
            "hiveplane_budget_exceeded_total",
            unit="{event}",
            description="Budget exhaustion events by level.",
        )
        self._certifications = meter.create_counter(
            "hiveplane_certifications_total",
            unit="{certification}",
            description="Certification runs by outcome.",
        )
        self._certification_duration = meter.create_histogram(
            "hiveplane_certification_duration_seconds",
            unit="s",
            description="Time to complete a certification run.",
        )
        self._attestation_verifications = meter.create_counter(
            "hiveplane_attestation_verifications_total",
            unit="{verification}",
            description="Attestation verifications on read.",
        )
        self._model_swap_blocks = meter.create_counter(
            "hiveplane_model_swap_blocks_total",
            unit="{block}",
            description="Model-swap blocks at admission.",
        )
        self._regressions = meter.create_counter(
            "hiveplane_regressions_caught_total",
            unit="{regression}",
            description="Regressions caught by re-certification.",
        )

    def record_run_state(self, *, workload: str, team: str | None, state: str) -> None:
        """Count a run entering a state."""
        self._runs.add(1, _attrs(workload=workload, team=team, state=state))

    def record_run_duration(
        self, *, workload: str, team: str | None, seconds: float
    ) -> None:
        """Record a terminal run's duration."""
        self._run_duration.record(seconds, _attrs(workload=workload, team=team))

    def record_failure(self, *, workload: str, team: str | None, reason: str) -> None:
        """Count a failed run."""
        self._failures.add(1, _attrs(workload=workload, team=team, reason=reason))

    def record_escalation(self, *, workload: str, team: str | None, rule: str) -> None:
        """Count an escalation."""
        self._escalations.add(1, _attrs(workload=workload, team=team, policy_rule=rule))

    def record_intervention_latency(self, *, workload: str, seconds: float) -> None:
        """Record time from the run's last change to an operator action."""
        self._intervention_latency.record(seconds, _attrs(workload=workload))

    def record_tool_call(
        self,
        *,
        workload: str,
        team: str | None,
        tool_id: str,
        trust_level: str,
        outcome: str,
    ) -> None:
        """Count a tool call and its boundary outcome."""
        self._tool_calls.add(
            1,
            _attrs(
                workload=workload,
                team=team,
                tool_id=tool_id,
                trust_level=trust_level,
                outcome=outcome,
            ),
        )

    def record_policy_decision(
        self, *, workload: str, team: str | None, decision: str, rule: str
    ) -> None:
        """Count a policy decision."""
        self._policy_decisions.add(
            1, _attrs(workload=workload, team=team, decision=decision, rule=rule)
        )

    def record_budget_burn(
        self, *, workload: str, team: str | None, run_id: str, cost_usd: float
    ) -> None:
        """Set the cumulative cost of a run."""
        self._budget_burn.set(
            cost_usd, _attrs(workload=workload, team=team, run_id=run_id)
        )

    def record_spend(
        self, *, workload: str, team: str | None, model: str, cost_usd: float
    ) -> None:
        """Attribute priced spend."""
        self._spend.add(
            cost_usd, _attrs(workload=workload, team=team, model=model)
        )

    def record_budget_exceeded(
        self, *, workload: str, team: str | None, level: str
    ) -> None:
        """Count a budget exhaustion event."""
        self._budget_exceeded.add(
            1, _attrs(workload=workload, team=team, level=level)
        )

    def record_certification(
        self, *, workload: str, team: str | None, status: str, duration_seconds: float
    ) -> None:
        """Count a certification outcome and record its duration."""
        attributes = _attrs(workload=workload, team=team, status=status)
        self._certifications.add(1, attributes)
        self._certification_duration.record(
            duration_seconds, _attrs(workload=workload, team=team)
        )

    def record_attestation_verification(self, *, workload: str, result: str) -> None:
        """Count an attestation verification on read."""
        self._attestation_verifications.add(
            1, _attrs(workload=workload, result=result)
        )

    def record_model_swap_block(self, *, workload: str) -> None:
        """Count a model-swap block."""
        self._model_swap_blocks.add(1, _attrs(workload=workload))

    def record_regression(self, *, workload: str) -> None:
        """Count a regression caught by re-certification."""
        self._regressions.add(1, _attrs(workload=workload))


def _attrs(**items: str | None) -> dict[str, AttributeValue]:
    """Build attributes, dropping absent (None) labels."""
    return {key: value for key, value in items.items() if value is not None}


_metrics: FleetMetrics = NullFleetMetrics()


def get_metrics() -> FleetMetrics:
    """Return the active metrics sink (no-op until telemetry is configured)."""
    return _metrics


def set_metrics(metrics: FleetMetrics) -> None:
    """Install the process-wide metrics sink."""
    global _metrics
    _metrics = metrics
