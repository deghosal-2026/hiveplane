"""Tests for the `hiveplane pipelines` CLI commands (M29)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()

_TIMELINE = {
    "pipeline_run_id": "pr-1",
    "pipeline_id": "incident-response",
    "state": "completed",
    "budget_usd": 25.0,
    "spent_usd": 0.3,
    "error": None,
    "nodes": [
        {
            "node_id": "triage",
            "status": "completed",
            "cost_usd": 0.1,
            "attempt": 1,
        }
    ],
}


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_pipelines_list(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            [{"id": "incident-response", "version": 1, "name": "IR"}]
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["pipelines", "list"])
    assert result.exit_code == 0
    assert "incident-response" in result.output


def test_pipelines_show(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (200, _json({"id": "incident-response"})),
    )
    result = runner.invoke(app, ["pipelines", "show", "incident-response"])
    assert result.exit_code == 0
    assert '"id": "incident-response"' in result.output


def test_pipelines_submit(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((url, payload))
        return 200, _json(_TIMELINE)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["pipelines", "submit", "--pipeline", "incident-response"])
    assert result.exit_code == 0
    assert calls[0][0].endswith("/pipelines/incident-response/runs")
    assert "completed" in result.output
    assert "triage" in result.output


def test_pipelines_status(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (200, _json(_TIMELINE))
    )
    result = runner.invoke(app, ["pipelines", "status", "pr-1"])
    assert result.exit_code == 0
    assert "incident-response" in result.output


def test_pipelines_retry(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, _json(_TIMELINE)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app, ["pipelines", "retry", "--run", "pr-1", "--node", "triage"]
    )
    assert result.exit_code == 0
    assert calls[0].endswith("/pipeline-runs/pr-1/nodes/triage/retry")
