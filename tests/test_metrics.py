"""Tests for the fleet/certification metrics sink (M20, #50/#51)."""

from __future__ import annotations

import pytest

from hiveplane import metrics
from hiveplane.metrics import NullFleetMetrics, OtelFleetMetrics, get_metrics, set_metrics
from metrics import MetricReader


def test_run_state_counter_records_labels(metric_reader: MetricReader) -> None:
    get_metrics().record_run_state(workload="agent-1", team="payments", state="completed")

    points = metric_reader.points("hiveplane_runs_total")
    assert len(points) == 1
    attributes, value = points[0]
    assert value == 1
    assert attributes == {
        "workload": "agent-1",
        "team": "payments",
        "state": "completed",
    }


def test_team_is_omitted_when_absent(metric_reader: MetricReader) -> None:
    get_metrics().record_run_state(workload="agent-1", team=None, state="queued")

    attributes, _ = metric_reader.points("hiveplane_runs_total")[0]
    assert "team" not in attributes


def test_run_duration_histogram_records_seconds(metric_reader: MetricReader) -> None:
    get_metrics().record_run_duration(workload="agent-1", team="payments", seconds=2.5)

    points = metric_reader.points("hiveplane_run_duration_seconds")
    assert len(points) == 1
    attributes, value = points[0]
    assert value == 2.5
    assert attributes["workload"] == "agent-1"
    assert attributes["team"] == "payments"


def test_budget_burn_gauge_records_cost(metric_reader: MetricReader) -> None:
    get_metrics().record_budget_burn(
        workload="agent-1", team="payments", run_id="run-1", cost_usd=0.25
    )

    points = metric_reader.points("hiveplane_budget_burn_usd")
    assert len(points) == 1
    attributes, value = points[0]
    assert value == 0.25
    assert attributes["run_id"] == "run-1"


def test_counters_use_expected_names(metric_reader: MetricReader) -> None:
    sink = get_metrics()
    sink.record_failure(workload="agent-1", team="payments", reason="timeout")
    sink.record_escalation(workload="agent-1", team="payments", rule="trust.destructive")
    sink.record_tool_call(
        workload="agent-1",
        team="payments",
        tool_id="mcp.t.read",
        trust_level="read_only",
        outcome="allowed",
    )
    sink.record_policy_decision(
        workload="agent-1", team="payments", decision="allow", rule="manifest.allow"
    )
    sink.record_spend(
        workload="agent-1", team="payments", model="openai:gpt-4o", cost_usd=0.5
    )
    sink.record_budget_exceeded(workload="agent-1", team="payments", level="run")
    sink.record_certification(
        workload="agent-1", team="payments", status="certified", duration_seconds=1.0
    )
    sink.record_attestation_verification(workload="agent-1", result="verified")
    sink.record_model_swap_block(workload="agent-1")
    sink.record_regression(workload="agent-1")
    sink.record_intervention_latency(workload="agent-1", seconds=3.0)

    for name in (
        "hiveplane_failures_total",
        "hiveplane_escalations_total",
        "hiveplane_tool_calls_total",
        "hiveplane_policy_decisions_total",
        "hiveplane_spend_usd_total",
        "hiveplane_budget_exceeded_total",
        "hiveplane_certifications_total",
        "hiveplane_certification_duration_seconds",
        "hiveplane_attestation_verifications_total",
        "hiveplane_model_swap_blocks_total",
        "hiveplane_regressions_caught_total",
        "hiveplane_intervention_latency_seconds",
    ):
        assert metric_reader.points(name), f"{name} was not recorded"


def test_null_metrics_accepts_every_signal() -> None:
    sink = NullFleetMetrics()

    sink.record_run_state(workload="a", team=None, state="queued")
    sink.record_run_duration(workload="a", team=None, seconds=0.0)
    sink.record_failure(workload="a", team=None, reason="x")
    sink.record_escalation(workload="a", team=None, rule="r")
    sink.record_intervention_latency(workload="a", seconds=0.0)
    sink.record_tool_call(
        workload="a", team=None, tool_id="t", trust_level="read_only", outcome="allowed"
    )
    sink.record_policy_decision(workload="a", team=None, decision="allow", rule="r")
    sink.record_budget_burn(workload="a", team=None, run_id="run", cost_usd=0.0)
    sink.record_spend(workload="a", team=None, model="m", cost_usd=0.0)
    sink.record_budget_exceeded(workload="a", team=None, level="run")
    sink.record_certification(
        workload="a", team=None, status="certified", duration_seconds=0.0
    )
    sink.record_attestation_verification(workload="a", result="verified")
    sink.record_model_swap_block(workload="a")
    sink.record_regression(workload="a")


def test_set_metrics_replaces_the_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    from hiveplane import metrics as module

    monkeypatch.setattr(module, "_metrics", NullFleetMetrics())
    replacement = NullFleetMetrics()
    set_metrics(replacement)
    try:
        assert get_metrics() is replacement
    finally:
        set_metrics(NullFleetMetrics())


def test_otel_metrics_uses_a_meter() -> None:
    reader = MetricReader()
    sink = OtelFleetMetrics(reader.meter)

    sink.record_model_swap_block(workload="agent-1")

    assert reader.points("hiveplane_model_swap_blocks_total") == [
        ({"workload": "agent-1"}, 1)
    ]


def test_default_sink_is_null() -> None:
    assert isinstance(metrics.NullFleetMetrics(), NullFleetMetrics)
