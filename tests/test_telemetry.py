"""Tests for the OpenTelemetry bootstrap and span helpers (M19, #48)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from hiveplane.config import OtelSettings
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.telemetry import (
    CERTIFICATION_STATUS,
    MODEL_IDENTITY,
    RUN_ID,
    TEAM,
    WORKLOAD,
    build_tracer_provider,
    configure_telemetry,
    run_attributes,
    span,
)
from telemetry import SpanRecorder


def _run_and_workload(
    make_manifest: Callable[..., AgentWorkload],
    *,
    run_id: str = "run-1",
    model_identity: str | None = "openai:gpt-4o:2024-08-06",
) -> tuple[Run, AgentWorkload]:
    workload = make_manifest(name="agent-a", team="payments")
    run = Run(
        id=run_id,
        workload_id=workload.name,
        caller="api",
        state=RunState.QUEUED,
        model_identity=model_identity,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        context=AdmissionContext.PRODUCTION,
    )
    return run, workload


def test_run_attributes_include_correlation_ids(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    run, workload = _run_and_workload(make_manifest)

    attributes = run_attributes(run, workload)

    assert attributes[RUN_ID] == "run-1"
    assert attributes[WORKLOAD] == "agent-a"
    assert attributes[TEAM] == "payments"
    assert attributes[MODEL_IDENTITY] == "openai:gpt-4o:2024-08-06"
    assert attributes[CERTIFICATION_STATUS] == "uncertified"


def test_run_attributes_omit_team_without_workload(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    run, _ = _run_and_workload(make_manifest)

    attributes = run_attributes(run)

    assert TEAM not in attributes
    assert attributes[RUN_ID] == "run-1"
    assert attributes[WORKLOAD] == "agent-a"


def test_run_attributes_omit_model_identity_when_unset(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    run, workload = _run_and_workload(make_manifest, model_identity=None)

    attributes = run_attributes(run, workload)

    assert MODEL_IDENTITY not in attributes


def test_span_records_name_and_run_attributes(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    run, workload = _run_and_workload(make_manifest)

    with span("admission", run=run, workload=workload):
        pass

    recorded = telemetry_spans.find("admission")
    assert recorded.attributes is not None
    assert recorded.attributes[RUN_ID] == "run-1"
    assert recorded.attributes[WORKLOAD] == "agent-a"
    assert recorded.attributes[TEAM] == "payments"


def test_span_merges_extra_attributes(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    run, workload = _run_and_workload(make_manifest)

    with span("tool_call", run=run, workload=workload, attributes={"tool_id": "search"}):
        pass

    recorded = telemetry_spans.find("tool_call")
    assert recorded.attributes is not None
    assert recorded.attributes["tool_id"] == "search"
    assert recorded.attributes[RUN_ID] == "run-1"


def test_span_nests_child_under_parent(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    run, workload = _run_and_workload(make_manifest)

    with (
        span("execution", run=run, workload=workload),
        span("tool_call", run=run, workload=workload),
    ):
        pass

    child = telemetry_spans.find("tool_call")
    parent = telemetry_spans.find("execution")
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id


def test_span_yields_active_span_for_late_attributes(
    telemetry_spans: SpanRecorder,
) -> None:
    with span("certification") as active:
        active.set_attribute("workload", "agent-a")

    recorded = telemetry_spans.find("certification")
    assert recorded.attributes is not None
    assert recorded.attributes["workload"] == "agent-a"


def test_build_tracer_provider_exports_with_service_name() -> None:
    exporter = InMemorySpanExporter()

    provider = build_tracer_provider(
        OtelSettings(service_name="hiveplane-test"), exporter=exporter
    )
    tracer = provider.get_tracer("hiveplane")
    with tracer.start_as_current_span("probe"):
        pass
    provider.shutdown()

    spans = exporter.get_finished_spans()
    assert [recorded.name for recorded in spans] == ["probe"]
    assert spans[0].resource.attributes["service.name"] == "hiveplane-test"


def test_configure_telemetry_installs_provider_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiveplane import telemetry

    monkeypatch.setattr(telemetry, "_provider", None)
    first = configure_telemetry(OtelSettings(), exporter=InMemorySpanExporter())
    second = configure_telemetry(OtelSettings(), exporter=InMemorySpanExporter())

    assert first is second


def test_configure_telemetry_builds_otlp_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiveplane import telemetry

    monkeypatch.setattr(telemetry, "_provider", None)
    provider = configure_telemetry(OtelSettings(endpoint="http://collector:4318"))
    try:
        assert provider.resource.attributes["service.name"] == "hiveplane"
    finally:
        provider.shutdown()
        monkeypatch.setattr(telemetry, "_provider", None)
