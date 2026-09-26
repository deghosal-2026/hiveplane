"""Tests for the `hiveplane drift` CLI commands (M34)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def test_drift_due_lists(monkeypatch: Any) -> None:
    response_body = [
        {
            "workload": "repo-agent",
            "next_re_cert_run": "2026-09-26T10:00:00Z",
            "expires_at": None,
            "overdue": True,
            "expiry_state": "unknown",
        }
    ]
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (200, json.dumps(response_body)),
    )
    result = runner.invoke(app, ["drift", "due"])
    assert result.exit_code == 0
    assert "repo-agent" in result.output


def test_drift_due_empty(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (200, "[]"),
    )
    result = runner.invoke(app, ["drift", "due"])
    assert result.exit_code == 0
    assert "No workloads" in result.output


def test_drift_assess_posts_summary(monkeypatch: Any) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((url, payload))
        return 200, json.dumps({"workload": "repo-agent", "verdict": "stable"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app,
        ["drift", "assess", "repo-agent", "--pass-rate", "0.9", "--tasks-failed", "2"],
    )
    assert result.exit_code == 0
    assert calls[0][0].endswith("/drift/assess")
    assert calls[0][1]["current"]["tasks_failed"] == 2


def test_drift_probe_forwards_options(monkeypatch: Any) -> None:
    calls: list[Any] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(payload)
        return 200, json.dumps({"workload": "repo-agent", "verdict": "stable"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app, ["drift", "probe", "repo-agent", "--context", "production", "--corpus", "c.yaml"]
    )
    assert result.exit_code == 0
    assert calls[0]["target_context"] == "production"
    assert calls[0]["corpus"] == "c.yaml"


def test_drift_quarantine_and_reinstate(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        if url.endswith("/reinstate"):
            return 200, json.dumps(
                {
                    "quarantine_id": "quar-1",
                    "workload": "repo-agent",
                    "status": "reinstated",
                    "reinstated_by": "alice",
                }
            )
        return 201, json.dumps(
            {
                "quarantine_id": "quar-1",
                "workload": "repo-agent",
                "status": "active",
                "severity": "critical",
                "reason": "drift",
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    quarantined = runner.invoke(
        app, ["drift", "quarantine", "repo-agent", "--reason", "drift"]
    )
    assert quarantined.exit_code == 0
    assert calls[0].endswith("/quarantines")

    reinstated = runner.invoke(
        app, ["drift", "reinstate", "quar-1", "--operator", "alice"]
    )
    assert reinstated.exit_code == 0
    assert calls[1].endswith("/quarantines/quar-1/reinstate")
    assert "alice" in reinstated.output


def test_drift_quarantines_lists(monkeypatch: Any) -> None:
    response_body = [
        {
            "quarantine_id": "quar-1",
            "workload": "repo-agent",
            "status": "active",
            "severity": "critical",
            "reason": "drift",
        }
    ]
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (200, json.dumps(response_body)),
    )
    result = runner.invoke(app, ["drift", "quarantines"])
    assert result.exit_code == 0
    assert "drift" in result.output


def test_drift_command_failure_exits_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (503, json.dumps({"detail": "disabled"})),
    )
    result = runner.invoke(app, ["drift", "schedules"])
    assert result.exit_code == 1


def test_drift_expiries_lists(monkeypatch: Any) -> None:
    response_body = [
        {
            "workload": "repo-agent",
            "state": "expiring",
            "expires_at": "2026-09-27T10:00:00Z",
            "days_remaining": 1.0,
            "renewable": True,
        }
    ]
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (200, json.dumps(response_body)),
    )
    result = runner.invoke(app, ["drift", "expiries"])
    assert result.exit_code == 0
    assert "expiring" in result.output
