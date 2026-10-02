"""Pipeline store tests: in-memory and Postgres (M29-02)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from hiveplane.fleet.pipelines import PipelineState
from hiveplane.pipelines.models import NodeStatus, PipelineNodeRun, PipelineRunHeader
from hiveplane.pipelines.spec import PipelineSpec
from hiveplane.pipelines.store import (
    InMemoryPipelineStore,
    PipelineStore,
    PostgresPipelineStore,
)
from postgres import reset_database

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec() -> PipelineSpec:
    return PipelineSpec.model_validate(
        {
            "id": "incident-response",
            "name": "Incident response",
            "nodes": [{"id": "triage", "kind": "workload", "workload": "triage"}],
        }
    )


def _header(state: PipelineState = PipelineState.RUNNING) -> PipelineRunHeader:
    return PipelineRunHeader(
        pipeline_run_id="pr-1",
        pipeline_id="incident-response",
        state=state,
        budget_usd=25.0,
        started_at=_NOW,
    )


def _node(attempt: int = 1, status: NodeStatus = NodeStatus.RUNNING) -> PipelineNodeRun:
    return PipelineNodeRun(
        pipeline_run_id="pr-1",
        pipeline_id="incident-response",
        node_id="triage",
        attempt=attempt,
        status=status,
        child_run_id=f"run-{attempt}",
        cost_usd=1.5,
    )


def _assert_store(store: PipelineStore) -> None:
    store.save_spec(_spec())
    loaded = store.get_spec("incident-response")
    assert loaded is not None and loaded.name == "Incident response"
    assert [s.id for s in store.list_specs()] == ["incident-response"]
    assert store.get_spec("missing") is None

    store.save_run(_header())
    header = store.get_run("pr-1")
    assert header is not None and header.state is PipelineState.RUNNING
    store.save_run(_header(PipelineState.COMPLETED))
    completed = store.get_run("pr-1")
    assert completed is not None and completed.state is PipelineState.COMPLETED
    assert [r.pipeline_run_id for r in store.list_runs("incident-response")] == ["pr-1"]

    store.save_node_run(_node(1))
    store.save_node_run(_node(2, NodeStatus.FAILED))
    first = store.get_node_run("pr-1", "triage", 1)
    second = store.get_node_run("pr-1", "triage", 2)
    assert first is not None and first.status is NodeStatus.RUNNING
    assert second is not None and second.status is NodeStatus.FAILED
    assert store.get_node_run("pr-1", "triage", 9) is None
    assert [n.attempt for n in store.list_node_runs("pr-1")] == [1, 2]

    store.clear()
    assert store.list_specs() == []
    assert store.list_runs() == []
    assert store.list_node_runs("pr-1") == []


def test_in_memory_pipeline_store() -> None:
    _assert_store(InMemoryPipelineStore())


def test_postgres_pipeline_store(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    _assert_store(PostgresPipelineStore(pg_engine))
