"""Tests for the progressive-delivery CLI (M37-06, M38)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _patch(monkeypatch: Any, payload: object, captured: dict[str, Any]) -> None:
    def _fake(
        method: str, url: str, body: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = body
        return 200, json.dumps(payload)

    monkeypatch.setattr("hiveplane.cli._request", _fake)


def test_shadow_report(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"shadow_run_id": "shadow-1", "outcome_diff": {}}, captured)

    result = runner.invoke(app, ["shadow", "report", "shadow-1"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/shadow/shadow-1/report")
    assert "shadow-1" in result.output


def test_canary_start_posts_the_split(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"rollout_id": "canary-1", "state": "active"}, captured)

    result = runner.invoke(
        app, ["canary", "start", "repo-agent", "--candidate", "2", "--pct", "10"]
    )

    assert result.exit_code == 0
    assert captured["payload"]["candidate_version"] == 2
    assert captured["payload"]["traffic_pct"] == 10
    assert captured["url"].endswith("/canary")


def test_canary_status(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"rollout_id": "canary-1", "candidate_samples": 5}, captured)

    result = runner.invoke(app, ["canary", "status", "canary-1"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/canary/canary-1")


def test_canary_promote_sends_operator_and_reason(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"rollout_id": "canary-1", "state": "promoted"}, captured)

    result = runner.invoke(
        app, ["canary", "promote", "canary-1", "--operator", "alice", "--reason", "lgtm"]
    )

    assert result.exit_code == 0
    assert captured["url"].endswith("/canary/canary-1/promote")
    assert captured["payload"] == {"operator": "alice", "reason": "lgtm"}


def test_canary_abort(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"rollout_id": "canary-1", "state": "rolled_back"}, captured)

    result = runner.invoke(app, ["canary", "abort", "canary-1"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/canary/canary-1/abort")


def test_canary_reports_api_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (404, json.dumps({"detail": "not found"})),
    )

    result = runner.invoke(app, ["canary", "status", "ghost"])

    assert result.exit_code == 1


def test_experiment_start(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, {"campaign_id": "campaign-1", "state": "running"}, captured)

    result = runner.invoke(
        app, ["experiment", "start", "repo-agent", "--arms", "gpt-4o,gpt-4o-mini"]
    )

    assert result.exit_code == 0
    assert captured["payload"]["arms"] == ["gpt-4o", "gpt-4o-mini"]
    assert captured["url"].endswith("/experiments")
