"""Tests for the pipeline API (M29)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.pipelines.engine import PipelineEngine
from test_pipelines_engine import FakeExecutor, _linear, _outputs


def _client() -> TestClient:
    app = create_app()
    app.state.pipeline_engine = PipelineEngine(
        app.state.pipeline_store, FakeExecutor(_outputs)
    )
    return TestClient(app)


def _spec() -> dict[str, object]:
    return _linear().model_dump(mode="json")


def test_pipeline_crud_and_submit() -> None:
    client = _client()
    assert client.post("/pipelines", json=_spec()).status_code == 201
    assert client.get("/pipelines").json()[0]["id"] == "incident-response"
    assert client.get("/pipelines/incident-response").json()["name"] == "Incident response"
    assert client.get("/pipelines/missing").status_code == 404

    response = client.post("/pipelines/incident-response/runs", json={"inputs": {}})
    assert response.status_code == 200
    timeline = response.json()
    assert timeline["state"] == "completed"
    assert [node["node_id"] for node in timeline["nodes"]] == [
        "triage",
        "remediate",
        "notify",
    ]

    run_id = timeline["pipeline_run_id"]
    assert client.get(f"/pipeline-runs/{run_id}").json()["state"] == "completed"
    assert client.get("/pipeline-runs").json()[0]["pipeline_id"] == "incident-response"


def test_submit_unknown_pipeline_is_not_found() -> None:
    client = _client()
    assert client.post("/pipelines/missing/runs", json={}).status_code == 404


def test_get_unknown_pipeline_run_is_not_found() -> None:
    client = _client()
    assert client.get("/pipeline-runs/missing").status_code == 404


def test_retry_failed_node() -> None:
    from hiveplane.pipelines.models import NodeResult, NodeStatus

    attempts = {"n": 0}

    def factory(workload: str, inputs: dict[str, object]) -> NodeResult:
        if workload == "triage":
            attempts["n"] += 1
            if attempts["n"] == 1:
                return NodeResult(status=NodeStatus.FAILED, error="boom")
        return _outputs(workload, inputs)

    app = create_app()
    app.state.pipeline_engine = PipelineEngine(
        app.state.pipeline_store, FakeExecutor(factory)
    )
    client = TestClient(app)
    client.post("/pipelines", json=_spec())
    timeline = client.post("/pipelines/incident-response/runs", json={}).json()
    assert timeline["state"] == "failed"
    run_id = timeline["pipeline_run_id"]
    retried = client.post(f"/pipeline-runs/{run_id}/nodes/triage/retry").json()
    assert retried["state"] == "completed"
    assert client.post("/pipeline-runs/missing/nodes/triage/retry").status_code == 404
