"""Tests for the replay API (M60-06)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload

_TENANT = {"X-Hiveplane-Tenant": "default"}


def _manifest_payload(make_manifest: Callable[..., AgentWorkload]) -> dict[str, object]:
    return make_manifest(name="repo-agent").model_dump(by_alias=True, mode="json")


def _seed_run(client: TestClient, make_manifest: Callable[..., AgentWorkload]) -> str:
    client.post("/workloads", json=_manifest_payload(make_manifest), headers=_TENANT)
    run = client.post(
        "/runs",
        json={
            "workload": "repo-agent",
            "caller": "cli",
            "context": "sandbox",
            "task": {"ticket": "T-1"},
        },
        headers=_TENANT,
    )
    return str(run.json()["id"])


def test_replay_endpoint_reconstructs_frames(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    run_id = _seed_run(client, make_manifest)

    response = client.post(f"/replay/{run_id}", headers=_TENANT)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["frame_count"] >= 1
    assert body["side_effects"] is False
    assert body["digest"]


def test_replay_lists_records(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    run_id = _seed_run(client, make_manifest)
    client.post(f"/replay/{run_id}", headers=_TENANT)

    response = client.get("/replays", headers=_TENANT)

    assert response.status_code == 200
    assert [record["mode"] for record in response.json()] == ["replay"]


def test_replay_unknown_run_is_404() -> None:
    app = create_app()
    client = TestClient(app)
    assert client.post("/replay/ghost", headers=_TENANT).status_code == 404


def test_fork_endpoint_creates_a_read_only_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    run_id = _seed_run(client, make_manifest)

    response = client.post(
        f"/runs/{run_id}/fork",
        json={"edits": {"priority": "high"}},
        headers=_TENANT,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_run_id"] == run_id
    assert body["side_effects"] is False
    assert body["task"] == {"ticket": "T-1", "priority": "high"}
    forked = client.get(f"/runs/{body['forked_run_id']}", headers=_TENANT).json()
    assert forked["read_only"] is True


def test_ab_replay_endpoint_runs_both_arms(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    run_id = _seed_run(client, make_manifest)

    response = client.post(
        "/replay/ab",
        json={"source_run_id": run_id, "workload_a": "repo-agent", "workload_b": "repo-agent"},
        headers=_TENANT,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["run_a_id"] != body["run_b_id"]
    assert body["diff"]["source_run_id"] == body["run_a_id"]


def test_run_diff_endpoint(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    run_id = _seed_run(client, make_manifest)

    response = client.get(
        "/replay/diff", params={"run_a": run_id, "run_b": run_id}, headers=_TENANT
    )

    assert response.status_code == 200
    assert response.json()["identical"] is True
