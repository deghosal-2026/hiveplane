"""CLI tests for incident mode (M53)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_fleet_pause_posts_and_reports(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(method=method, url=url, payload=payload)
        return 200, _json(
            {
                "incident_id": "inc-1",
                "scope": "fleet",
                "scope_ref": None,
                "trigger": "operator",
                "reason": "prod down",
                "actor": "cli",
                "halted_at": "2026-09-27T12:00:00Z",
                "resumed_at": None,
                "resumed_by": None,
                "owners_notified": ["slack:#ops"],
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["fleet", "pause", "--reason", "prod down"])

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/fleet/pause")
    assert captured["payload"]["scope"] == "fleet"
    assert "halted" in result.output


def test_fleet_resume_posts(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(method=method, url=url, payload=payload)
        return 200, _json(
            {
                "incident_id": "inc-1",
                "resumed_at": "2026-09-27T12:05:00Z",
                "resumed_by": "bob",
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["fleet", "resume", "--actor", "bob"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/fleet/resume")
    assert captured["payload"]["actor"] == "bob"
    assert "resumed by bob" in result.output


def test_fleet_status_running(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json({"halted": False, "active": None, "history": []})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["fleet", "status"])

    assert result.exit_code == 0
    assert "RUNNING" in result.output


def test_fleet_status_halted(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            {
                "halted": True,
                "active": {
                    "incident_id": "inc-1",
                    "scope": "fleet",
                    "scope_ref": None,
                    "trigger": "operator",
                    "reason": "incident",
                    "actor": "alice",
                    "halted_at": "2026-09-27T12:00:00Z",
                    "resumed_at": None,
                    "resumed_by": None,
                    "owners_notified": [],
                },
                "history": [],
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["fleet", "status"])

    assert result.exit_code == 0
    assert "HALTED" in result.output
    assert "inc-1" in result.output


def test_fleet_pause_failure_exits_nonzero(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 403, "forbidden"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["fleet", "pause"])

    assert result.exit_code == 1
