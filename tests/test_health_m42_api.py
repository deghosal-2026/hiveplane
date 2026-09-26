"""Tests for the health API and CLI (M42-06)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hiveplane.api.app import create_app
from hiveplane.cli import app
from hiveplane.core.run import RunState
from hiveplane.health.service import HealthService
from test_health_m42 import _run, _service

runner = CliRunner()


def _client(
    quarantine: Callable[[str, str], None] | None = None,
) -> tuple[TestClient, HealthService]:
    app_obj = create_app()
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=20),
        _run("r3", state=RunState.FAILED, offset_seconds=30),
    ]
    service = _service(runs, quality=0.5, quarantine=quarantine)
    app_obj.state.health_service = service
    return TestClient(app_obj), service


def test_fleet_health_endpoint() -> None:
    client, _ = _client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()[0]["workload"] == "repo-agent"


def test_workload_health_and_slo_endpoints() -> None:
    client, _ = _client()

    health = client.get("/health/workloads/repo-agent")
    slo = client.get("/health/workloads/repo-agent/slo")
    burn = client.get("/health/workloads/repo-agent/burn")

    assert health.status_code == 200
    assert health.json()["failed_runs"] == 2
    assert slo.status_code == 200
    assert {entry["objective"] for entry in slo.json()} == {"availability", "quality"}
    assert burn.status_code == 200
    assert burn.json()["alert"] is True


def test_enforce_burn_through_quarantines() -> None:
    quarantined: list[str] = []
    client, _ = _client(quarantine=lambda name, _reason: quarantined.append(name))

    response = client.post("/health/workloads/repo-agent/enforce")

    assert response.status_code == 200
    assert response.json()["action"] == "quarantine"
    assert quarantined == ["repo-agent"]


def test_health_cli_list(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                [
                    {
                        "workload": "repo-agent",
                        "status": "degraded",
                        "failure_rate": 0.5,
                        "readiness": True,
                    }
                ]
            ),
        ),
    )

    result = runner.invoke(app, ["health", "list"])

    assert result.exit_code == 0
    assert "repo-agent" in result.output


def test_health_cli_show(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        return 200, json.dumps({"workload": "repo-agent", "status": "healthy"})

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["health", "show", "repo-agent"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/health/workloads/repo-agent")
