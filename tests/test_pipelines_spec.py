"""Tests for the pipeline spec DSL (M29-01)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from hiveplane.pipelines.spec import (
    NodeKind,
    OnExceed,
    OnFailure,
    PipelineSpec,
    Reducer,
)


def _linear() -> dict[str, Any]:
    return {
        "id": "incident-response",
        "name": "Incident response",
        "budget": {"usd": 25.0, "on_exceed": "pause"},
        "nodes": [
            {"id": "triage", "kind": "workload", "workload": "triage-agent"},
            {
                "id": "remediate",
                "kind": "workload",
                "workload": "remediation-agent",
                "inputs": {"diagnosis": "${triage.output.diagnosis}"},
                "requires_approval": "before",
                "budget": {"usd": 5.0},
            },
            {
                "id": "notify",
                "kind": "workload",
                "workload": "notify-agent",
                "inputs": {"summary": "${remediate.output.summary}"},
            },
        ],
        "edges": [
            {"from": "triage", "to": "remediate"},
            {"from": "remediate", "to": "notify"},
        ],
    }


def test_valid_linear_pipeline_parses() -> None:
    spec = PipelineSpec.model_validate(_linear())
    assert [node.id for node in spec.nodes] == ["triage", "remediate", "notify"]
    assert spec.nodes[1].on_failure is OnFailure.FAIL_FAST
    assert spec.budget.on_exceed is OnExceed.PAUSE
    assert spec.nodes[1].budget is not None and spec.nodes[1].budget.usd == 5.0


def test_duplicate_node_ids_are_rejected() -> None:
    data = _linear()
    data["nodes"] = [*data["nodes"], {"id": "triage", "kind": "workload", "workload": "x"}]
    with pytest.raises(ValidationError, match="unique"):
        PipelineSpec.model_validate(data)


def test_dangling_edge_is_rejected() -> None:
    data = _linear()
    data["edges"] = [*data["edges"], {"from": "notify", "to": "ghost"}]
    with pytest.raises(ValidationError, match="unknown"):
        PipelineSpec.model_validate(data)


def test_self_loop_is_rejected() -> None:
    data = _linear()
    data["edges"] = [*data["edges"], {"from": "notify", "to": "notify"}]
    with pytest.raises(ValidationError, match="self-loop"):
        PipelineSpec.model_validate(data)


def test_cycle_is_rejected_and_names_nodes() -> None:
    data = _linear()
    data["edges"] = [
        {"from": "triage", "to": "remediate"},
        {"from": "remediate", "to": "notify"},
        {"from": "notify", "to": "triage"},
    ]
    with pytest.raises(ValidationError) as exc:
        PipelineSpec.model_validate(data)
    message = str(exc.value)
    assert "cycle" in message
    assert "triage" in message


def test_workload_node_requires_workload() -> None:
    with pytest.raises(ValidationError, match="requires 'workload'"):
        PipelineSpec.model_validate(
            {"id": "p", "name": "p", "nodes": [{"id": "a", "kind": "workload"}]}
        )


def test_fan_out_requires_over_and_template() -> None:
    with pytest.raises(ValidationError, match="fan_out"):
        PipelineSpec.model_validate(
            {
                "id": "p",
                "name": "p",
                "nodes": [{"id": "f", "kind": "fan_out"}],
            }
        )


def test_fan_in_requires_from_and_reducer() -> None:
    with pytest.raises(ValidationError, match="fan_in"):
        PipelineSpec.model_validate(
            {"id": "p", "name": "p", "nodes": [{"id": "f", "kind": "fan_in"}]}
        )


def test_fan_out_and_fan_in_parse() -> None:
    spec = PipelineSpec.model_validate(
        {
            "id": "p",
            "name": "p",
            "nodes": [
                {"id": "triage", "kind": "workload", "workload": "triage"},
                {
                    "id": "fanout",
                    "kind": "fan_out",
                    "over": "${triage.output.services}",
                    "as": "service",
                    "node": "remediate",
                },
                {
                    "id": "remediate",
                    "kind": "workload",
                    "workload": "remediation",
                    "inputs": {"service": "${service}"},
                },
                {"id": "summary", "kind": "fan_in", "from": ["fanout"], "reducer": "json_merge"},
            ],
            "edges": [
                {"from": "triage", "to": "fanout"},
                {"from": "fanout", "to": "remediate"},
                {"from": "fanout", "to": "summary"},
            ],
        }
    )
    fanout = spec.node("fanout")
    assert fanout is not None and fanout.kind is NodeKind.FAN_OUT
    assert fanout.as_name == "service"
    summary = spec.node("summary")
    assert summary is not None and summary.reducer is Reducer.JSON_MERGE
    assert summary.from_nodes == ["fanout"]


def test_data_reference_to_unknown_node_is_rejected() -> None:
    data = _linear()
    data["nodes"][1]["inputs"] = {"diagnosis": "${ghost.output.diagnosis}"}
    with pytest.raises(ValidationError, match="ghost"):
        PipelineSpec.model_validate(data)


def test_data_reference_must_be_upstream() -> None:
    data = _linear()
    data["nodes"][0]["inputs"] = {"x": "${notify.output.summary}"}
    with pytest.raises(ValidationError, match="upstream"):
        PipelineSpec.model_validate(data)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate({**_linear(), "surprise": True})
