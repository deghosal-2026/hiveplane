"""Tests for the reconcile API (M26-04)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app

_WORKLOAD = """\
kind: workload
metadata: {name: agent-1, owner: platform-team, team: platform}
spec:
  runtime: {adapter: raw-worker, entrypoint: "examples.worker:run"}
  model:
    strategy: tiered
    identity: {provider: openai, family: gpt-4o, version: "2024-08-06"}
  certification:
    benchmark_corpus: corpora/agent-1/v1
    staging_threshold: 0.8
    production_threshold: 0.9
    status: uncertified
  budget: {per_run_usd: 0.5, per_day_usd: 5.0, per_team_usd: 50.0}
"""


def _client() -> TestClient:
    return TestClient(create_app())


def _body(path: Path, confirmed: bool = False) -> dict[str, object]:
    return {"kind": "directory", "path": str(path), "confirmed": confirmed}


def test_plan_reports_without_mutating(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(_WORKLOAD)
    client = _client()
    response = client.post("/reconcile/git-main/plan", json=_body(tmp_path))
    assert response.status_code == 200
    run = response.json()
    assert run["outcome"] == "planned"
    assert run["actions"][0]["kind"] == "register_workload"
    assert client.get("/workloads").json() == []


def test_apply_registers_and_status_reflects_it(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(_WORKLOAD)
    client = _client()
    response = client.post("/reconcile/git-main/apply", json=_body(tmp_path))
    assert response.status_code == 200
    assert response.json()["outcome"] == "applied"

    status = client.get("/reconcile/git-main").json()
    assert status["status"] == "in_sync"
    assert status["last_outcome"] == "applied"
    assert status["open_drift"] == 0

    runs = client.get("/reconcile/git-main/runs").json()
    assert len(runs) == 1
    assert client.get("/reconcile/git-main/drift").json() == []


def test_invalid_request_body_is_rejected(tmp_path: Path) -> None:
    client = _client()
    response = client.post("/reconcile/git-main/plan", json={"kind": "spaceship"})
    assert response.status_code == 422
