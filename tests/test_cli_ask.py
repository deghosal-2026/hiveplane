"""CLI tests for `ask`, `report`, `top`, `logs`, and `replay` (M53)."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_ask_prints_answer(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        assert url.endswith("/ask")
        assert payload == {"question": "why did run 7 fail?"}
        return 200, _json(
            {
                "question": "why did run 7 fail?",
                "intent": "run_failure",
                "answer": "Run 7 failed: tool timeout",
                "evidence": {},
                "citations": ["run:7"],
                "read_only": True,
                "requires_confirmation": False,
                "tenant_id": "default",
                "attributed_to": "cli",
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["ask", "why did run 7 fail?"])

    assert result.exit_code == 0
    assert "tool timeout" in result.output


def test_ask_json_mode(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            {"answer": "ok", "intent": "fleet_status", "requires_confirmation": False}
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["ask", "what is running?", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["intent"] == "fleet_status"


def test_report_composes_sections(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        if url.endswith("/health"):
            return 200, _json([{"workload": "a", "status": "healthy"}])
        if "/cost/showback" in url:
            return 200, _json({"total_cost_usd": 1.0, "rows": []})
        if "/cost/roi/fleet" in url:
            return 200, _json({"fleet_roi": 1.5, "rows": []})
        if url.endswith("/analytics/approvals"):
            return 200, _json({"total": 0})
        return 404, "not found"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["report", "--tenant", "default"])

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert "health" in body
    assert "cost" in body
    assert "roi" in body


def test_top_ranks_by_roi(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            {
                "fleet_roi": 1.0,
                "rows": [
                    {"workload_id": "low", "roi": 0.2, "expensive_low_value": True},
                    {"workload_id": "high", "roi": 3.0, "expensive_low_value": False},
                ],
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["top"])

    assert result.exit_code == 0
    assert result.output.index("high") < result.output.index("low")
    assert "FLAG" in result.output


def test_logs_prints_events(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        assert url.endswith("/runs/run-1/events")
        return 200, _json(
            [
                {
                    "timestamp": "2026-09-27T12:00:00Z",
                    "type": "state_change",
                    "actor": "system",
                    "detail": "queued",
                }
            ]
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["logs", "run-1"])

    assert result.exit_code == 0
    assert "state_change" in result.output


def test_replay_reconstructs_frames(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, _json(
            {
                "run_id": "run-1",
                "frames": [
                    {
                        "sequence": 0,
                        "event_type": "state_change",
                        "from_state": "queued",
                        "to_state": "running",
                        "detail": None,
                    }
                ],
                "frame_count": 1,
                "digest": "deadbeef",
                "side_effects": False,
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["replay", "run-1"])

    assert result.exit_code == 0
    assert "side-effect free" in result.output
    assert "[0]" in result.output


def test_ask_failure_exits_nonzero(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 500, "boom"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["ask", "anything"])

    assert result.exit_code == 1


def test_shell_completion_is_exposed() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "install-completion" in result.output
