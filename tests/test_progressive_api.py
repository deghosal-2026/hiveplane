"""Tests for the progressive-delivery API (M37-06, M38)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload
from hiveplane.progressive.canary import CanaryService
from hiveplane.progressive.shadow import ShadowService
from hiveplane.progressive.store import InMemoryProgressiveStore
from test_progressive_canary import _registry
from test_progressive_shadow import _Runner


def _manifest_payload(make_manifest: Callable[..., AgentWorkload]) -> dict[str, object]:
    return make_manifest(name="repo-agent").model_dump(by_alias=True, mode="json")


def test_shadow_api_starts_and_reports(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    client.post("/workloads", json=_manifest_payload(make_manifest))
    production = client.post(
        "/runs",
        json={"workload": "repo-agent", "caller": "cli", "context": "sandbox", "task": {}},
    ).json()
    app.state.shadow_service = ShadowService(
        InMemoryProgressiveStore(), runner=_Runner(), id_factory=lambda: "shadow-1"
    )

    response = client.post(
        "/shadow",
        json={
            "candidate_workload_id": "repo-agent",
            "production_run_id": production["id"],
            "candidate_version": 2,
        },
    )

    assert response.status_code == 201
    assert response.json()["production_run_id"] == production["id"]

    report = client.get("/shadow/shadow-1/report")
    assert report.status_code == 200
    assert report.json()["outcome_diff"]["output_changed"] is True


def test_shadow_unknown_production_run_is_404(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    app.state.shadow_service = ShadowService(
        InMemoryProgressiveStore(), runner=_Runner(), id_factory=lambda: "shadow-1"
    )

    response = client.post(
        "/shadow",
        json={"candidate_workload_id": "repo-agent", "production_run_id": "ghost"},
    )

    assert response.status_code == 404


def test_canary_api_start_status_and_override(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    app.state.canary_service = CanaryService(
        InMemoryProgressiveStore(),
        registry=_registry(make_manifest),
        id_factory=lambda: "canary-1",
    )

    started = client.post(
        "/canary",
        json={
            "workload_id": "repo-agent",
            "candidate_version": 2,
            "traffic_pct": 10,
            "window_seconds": 60,
            "min_sample": 1,
        },
    )
    assert started.status_code == 201
    assert started.json()["state"] == "active"

    status_response = client.get("/canary/canary-1")
    assert status_response.status_code == 200
    assert status_response.json()["rollout_id"] == "canary-1"

    promoted = client.post(
        "/canary/canary-1/promote", json={"operator": "alice", "reason": "looks good"}
    )
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "promoted"


def test_canary_api_abort(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    client = TestClient(app)
    app.state.canary_service = CanaryService(
        InMemoryProgressiveStore(),
        registry=_registry(make_manifest),
        id_factory=lambda: "canary-1",
    )
    client.post(
        "/canary",
        json={
            "workload_id": "repo-agent",
            "candidate_version": 2,
            "traffic_pct": 10,
            "window_seconds": 60,
            "min_sample": 1,
        },
    )

    aborted = client.post(
        "/canary/canary-1/abort", json={"operator": "alice", "reason": "regression"}
    )

    assert aborted.status_code == 200
    assert aborted.json()["state"] == "rolled_back"


def test_canary_unknown_is_404(make_manifest: Callable[..., AgentWorkload]) -> None:
    app = create_app()
    client = TestClient(app)

    assert client.get("/canary/ghost").status_code == 404


def test_experiment_api_start_arms_and_select() -> None:
    app = create_app()
    client = TestClient(app)

    started = client.post(
        "/experiments", json={"workload_id": "repo-agent", "arms": ["gpt-4o", "gpt-4o-mini"]}
    )
    assert started.status_code == 201
    campaign_id = started.json()["campaign_id"]

    arms = client.get(f"/experiments/{campaign_id}/arms")
    assert arms.status_code == 200
    assert len(arms.json()) == 2

    selected = client.post(f"/experiments/{campaign_id}/select")
    assert selected.status_code == 200
    assert selected.json()["state"] == "completed"
    assert selected.json()["winner_arm_id"] is None


def test_experiment_requires_two_arms() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/experiments", json={"workload_id": "repo-agent", "arms": ["gpt-4o"]}
    )

    assert response.status_code == 422
