"""Tests for the `hiveplane reconcile` CLI (M26-04)."""

from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()

_STATUS = {
    "source_id": "git-main",
    "source": "git",
    "status": "in_sync",
    "last_revision": "abc123",
    "last_reconcile_at": "2026-01-01T00:00:00Z",
    "open_drift": 0,
    "last_run_id": "rr-1",
    "last_outcome": "applied",
}

_RUN = {
    "run_id": "rr-1",
    "source_id": "git-main",
    "source": "git",
    "revision": "abc123",
    "mode": "plan",
    "started_at": "2026-01-01T00:00:00Z",
    "finished_at": "2026-01-01T00:00:01Z",
    "outcome": "planned",
    "actions": [
        {
            "action_id": "act-1",
            "kind": "register_workload",
            "object_kind": "workload",
            "object_ref": "agent-1",
            "status": "planned",
            "detail": None,
        }
    ],
    "error": None,
}


def test_reconcile_status_prints_summary(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (200, _json(_STATUS))
    )
    result = runner.invoke(app, ["reconcile", "status", "--source", "git-main"])
    assert result.exit_code == 0
    assert "git-main" in result.output
    assert "in_sync" in result.output
    assert "abc123" in result.output


def test_reconcile_plan_posts_and_prints_actions(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append((method, url, payload))
        return 200, _json(_RUN)

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app, ["reconcile", "plan", "--source", "git-main", "--path", "/tmp/fleet"]
    )
    assert result.exit_code == 0
    assert "planned" in result.output
    assert "register_workload" in result.output
    assert calls[0][1].endswith("/reconcile/git-main/plan")
    assert calls[0][2]["kind"] == "directory"
    assert calls[0][2]["path"] == "/tmp/fleet"


def test_reconcile_apply_posts_to_apply(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        calls.append(url)
        return 200, _json({**_RUN, "mode": "apply", "outcome": "applied"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(
        app,
        ["reconcile", "apply", "--source", "git-main", "--path", "/tmp/fleet", "--confirmed"],
    )
    assert result.exit_code == 0
    assert calls[0].endswith("/reconcile/git-main/apply")
    assert "applied" in result.output


def test_reconcile_directory_requires_path() -> None:
    result = runner.invoke(app, ["reconcile", "plan", "--source", "git-main"])
    assert result.exit_code == 1


def test_reconcile_git_requires_url() -> None:
    result = runner.invoke(
        app, ["reconcile", "plan", "--source", "git-main", "--kind", "git"]
    )
    assert result.exit_code == 1


def test_reconcile_reports_api_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (500, "boom")
    )
    result = runner.invoke(
        app, ["reconcile", "plan", "--source", "git-main", "--path", "/tmp/fleet"]
    )
    assert result.exit_code == 1


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload)
