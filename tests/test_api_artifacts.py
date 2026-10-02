"""API tests for artifact storage, retention, and bundles (M54)."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app

_WORKLOAD = {
    "apiVersion": "hiveplane/v1",
    "kind": "AgentWorkload",
    "metadata": {"name": "summarizer", "owner": "platform", "team": "platform"},
    "spec": {"runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"}},
}


def _client() -> TestClient:
    return TestClient(create_app())


def test_capture_list_and_fetch_artifact() -> None:
    client = _client()
    content = base64.b64encode(b"hello").decode()

    captured = client.post(
        "/artifacts",
        json={"run_id": "run-1", "filename": "report.txt", "content": content},
    )
    assert captured.status_code == 200
    artifact = captured.json()
    assert artifact["run_id"] == "run-1"
    assert artifact["size_bytes"] == 5
    assert artifact["content_hash"].startswith("sha256:")

    listed = client.get("/artifacts", params={"run_id": "run-1"})
    assert listed.status_code == 200
    assert [a["artifact_id"] for a in listed.json()] == [artifact["artifact_id"]]

    fetched = client.get(f"/artifacts/{artifact['artifact_id']}")
    assert fetched.status_code == 200
    body = client.get(f"/artifacts/{artifact['artifact_id']}/content")
    assert body.status_code == 200
    assert body.content == b"hello"


def test_capture_rejects_bad_base64() -> None:
    response = _client().post(
        "/artifacts",
        json={"run_id": "run-1", "filename": "x", "content": "not base64!!"},
    )
    assert response.status_code == 400


def test_missing_artifact_is_404() -> None:
    assert _client().get("/artifacts/ghost").status_code == 404


def test_retention_policy_and_purge() -> None:
    client = _client()
    policy = client.post(
        "/retention/policies",
        json={
            "policy_id": "short",
            "data_class": "run_output",
            "retain_days": 0,
        },
    )
    assert policy.status_code == 200
    assert [p["policy_id"] for p in client.get("/retention/policies").json()] == ["short"]

    content = base64.b64encode(b"expire me").decode()
    captured = client.post(
        "/artifacts",
        json={
            "run_id": "run-1",
            "filename": "a.txt",
            "content": content,
            "retention_policy_id": "short",
        },
    )
    assert captured.status_code == 200

    purged = client.post("/retention/purge")
    assert purged.status_code == 200
    assert purged.json()["purged"] == [captured.json()["artifact_id"]]
    assert client.get("/artifacts").json() == []


def test_export_then_import_round_trip() -> None:
    client = _client()

    exported = client.post("/export", json={"workload": _WORKLOAD})
    assert exported.status_code == 200
    bundle = exported.json()
    assert bundle["kind"] == "FleetBundle"
    assert bundle["provenance"] is not None

    plan = client.post("/import", json={"bundle": bundle})
    assert plan.status_code == 200
    body = plan.json()
    assert body["dry_run"] is True
    assert body["entries"][0]["kind"] == "workload"


def test_tampered_import_is_refused() -> None:
    client = _client()
    bundle = client.post("/export", json={"workload": _WORKLOAD}).json()
    bundle["workload"]["metadata"]["name"] = "evil"

    response = client.post("/import", json={"bundle": bundle})

    assert response.status_code == 400
