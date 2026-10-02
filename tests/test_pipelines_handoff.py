"""Tests for pipeline run attribution and the handoff layer (M29-03)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from hiveplane.core.run import PipelineOrigin, Run, RunState
from hiveplane.core.spec import parse_io_spec
from hiveplane.pipelines.handoff import (
    HandoffError,
    PipelineContext,
    map_inputs,
    render_ref,
    validate_boundary,
    validate_schema,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_pipeline_origin_and_io_spec_models() -> None:
    origin = PipelineOrigin(
        pipeline_run_id="pr-1", pipeline_id="incident-response", node_id="triage"
    )
    assert origin.attempt == 1
    run = Run(
        id="run-1",
        workload_id="agent-1",
        caller="pipeline",
        state=RunState.QUEUED,
        created_at=_NOW,
        updated_at=_NOW,
        pipeline_origin=origin,
    )
    assert run.pipeline_origin is not None
    assert run.pipeline_origin.node_id == "triage"
    io = parse_io_spec(
        {
            "input_schema": {"type": "object", "required": ["service"]},
            "output_schema": {"type": "object"},
        }
    )
    assert io is not None and io.input_schema is not None


def _context() -> PipelineContext:
    return PipelineContext(
        outputs={
            "triage": {"output": {"diagnosis": "db down", "services": ["a", "b"]}},
            "remediate": {"output": {"summary": "fixed"}},
        },
        variables={"service": "a"},
    )


def test_render_ref_preserves_type_for_whole_placeholder() -> None:
    assert render_ref("${triage.output.services}", _context()) == ["a", "b"]
    assert render_ref("${service}", _context()) == "a"


def test_render_ref_interpolates_scalars() -> None:
    assert render_ref("service ${service} down", _context()) == "service a down"


def test_render_ref_missing_path_raises() -> None:
    with pytest.raises(HandoffError):
        render_ref("${triage.output.missing}", _context())
    with pytest.raises(HandoffError):
        render_ref("${ghost.output.x}", _context())


def test_map_inputs_renders_each_field() -> None:
    mapped = map_inputs(
        {"service": "${service}", "diagnosis": "${triage.output.diagnosis}"}, _context()
    )
    assert mapped == {"service": "a", "diagnosis": "db down"}


def test_validate_schema_type_and_required() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["service"],
        "properties": {"service": {"type": "string"}},
    }
    validate_schema({"service": "a"}, schema)
    with pytest.raises(HandoffError, match="required"):
        validate_schema({}, schema)
    with pytest.raises(HandoffError, match="service"):
        validate_schema({"service": 3}, schema)


def test_validate_boundary_reports_node_and_pointer() -> None:
    with pytest.raises(HandoffError) as exc:
        validate_boundary(
            {"service": 3},
            {"type": "object", "properties": {"service": {"type": "string"}}},
            node="remediate",
            boundary="input",
        )
    message = str(exc.value)
    assert "remediate" in message
    assert "input" in message


def test_validate_schema_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        from hiveplane.pipelines.handoff import JsonSchema

        JsonSchema.model_validate({"type": "spaceship"})
