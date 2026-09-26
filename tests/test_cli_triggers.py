"""Tests for the `hiveplane triggers` CLI commands (M28-08)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()

_TRIGGER = {
    "id": "t1",
    "source": "webhook",
    "target": {"kind": "workload", "ref": "agent-1"},
    "enabled": True,
}


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_triggers_list_service(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, _json([_TRIGGER])

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["triggers", "list"])
    assert result.exit_code == 0
    assert "t1" in result.output
    assert "enabled" in result.output
    assert calls[0].endswith("/triggers")


def test_triggers_list_workload_rules(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, _json([{"trigger_id": "t-1", "rule": {"type": "webhook"}}])

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["triggers", "list", "--workload", "agent-1"])
    assert result.exit_code == 0
    assert calls[0].endswith("/workloads/agent-1/triggers")


def test_triggers_show(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(_TRIGGER)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["triggers", "show", "t1"])
    assert result.exit_code == 0
    assert '"id": "t1"' in result.output


def test_triggers_create(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((url, payload))
        return 201, _json(_TRIGGER)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    spec = tmp_path / "trigger.yaml"
    spec.write_text("id: t1\nsource: webhook\ntarget: {kind: workload, ref: agent-1}\n")
    result = runner.invoke(app, ["triggers", "create", "--file", str(spec)])
    assert result.exit_code == 0
    assert calls[0][0].endswith("/triggers")


def test_triggers_test(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((url, payload))
        return 200, _json({"trigger_id": "t1", "dedup_key": None, "task": {"pr": 7}})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    payload = tmp_path / "payload.json"
    payload.write_text('{"number": 7}')
    result = runner.invoke(app, ["triggers", "test", "t1", "--payload", str(payload)])
    assert result.exit_code == 0
    assert "pr" in result.output
    assert calls[0][0].endswith("/triggers/t1/test")


def test_triggers_replay(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, _json({"outcome": "accepted", "run_id": "run-9"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["triggers", "replay", "dlq-1"])
    assert result.exit_code == 0
    assert calls[0].endswith("/triggers/dlq/dlq-1/replay")
    assert "run-9" in result.output


def test_triggers_enable_disable(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        enabled = url.endswith("/enable")
        return 200, _json({**_TRIGGER, "enabled": enabled})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    assert runner.invoke(app, ["triggers", "disable", "t1"]).exit_code == 0
    assert runner.invoke(app, ["triggers", "enable", "t1"]).exit_code == 0
    assert calls[0].endswith("/triggers/t1/disable")
    assert calls[1].endswith("/triggers/t1/enable")
