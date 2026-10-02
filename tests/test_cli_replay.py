"""Tests for the replay CLI (M60-06)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_replay_prints_frames_and_digest(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((method, url, payload))
        return 200, _json(
            {
                "run_id": "run-1",
                "frames": [
                    {
                        "sequence": 0,
                        "event_type": "admission",
                        "from_state": None,
                        "to_state": "queued",
                        "detail": "allowed",
                    }
                ],
                "frame_count": 1,
                "digest": "abc123",
                "side_effects": False,
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["replay", "run-1"])

    assert result.exit_code == 0
    assert "side-effect free" in result.output
    assert "[0]" in result.output
    assert "abc123" in result.output
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/replay/run-1")


def test_replay_fork_parses_edits(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((method, url, payload))
        return 201, _json(
            {
                "replay_id": "replay-1",
                "source_run_id": "run-1",
                "forked_run_id": "run-2",
                "task": {"ticket": "T-1", "priority": "high"},
                "fork_point": None,
                "side_effects": False,
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app, ["replay-fork", "run-1", "--edit", "priority=high"]
    )

    assert result.exit_code == 0
    assert "run-2" in result.output
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/runs/run-1/fork")
    assert calls[0][2]["edits"] == {"priority": "high"}


def test_replay_diff_reports_identical(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            {
                "identical": True,
                "result_changed": False,
                "cost_delta_usd": 0.0,
                "field_deltas": [],
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["replay-diff", "run-1", "run-2"])

    assert result.exit_code == 0
    assert "identical" in result.output.lower()


def test_replay_ab_prints_arms(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((method, url, payload))
        return 201, _json(
            {
                "replay_id": "replay-1",
                "source_run_id": "run-1",
                "run_a_id": "run-a",
                "run_b_id": "run-b",
                "workload_a": "agent-1",
                "workload_b": "agent-2",
                "side_effects": False,
                "diff": {"identical": True},
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app,
        ["replay-ab", "run-1", "--workload-a", "agent-1", "--workload-b", "agent-2"],
    )

    assert result.exit_code == 0
    assert "run-a" in result.output
    assert calls[0][1].endswith("/replay/ab")
    assert calls[0][2]["workload_a"] == "agent-1"
