"""CLI tenant/team scoping: global flags, headers, and clear errors (M58-05)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


class _FakeResponse:
    status = 200

    def read(self) -> bytes:
        return b"[]"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        return False


def _capture_request(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any) -> _FakeResponse:
        captured["headers"] = {
            key.lower(): value for key, value in request.headers.items()
        }
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_tenant_flag_sends_tenant_header(monkeypatch: Any) -> None:
    captured = _capture_request(monkeypatch)

    result = runner.invoke(app, ["--tenant", "acme", "runs", "list"])

    assert result.exit_code == 0, result.output
    assert captured["headers"]["x-hiveplane-tenant"] == "acme"


def test_team_flag_sends_team_header(monkeypatch: Any) -> None:
    captured = _capture_request(monkeypatch)

    result = runner.invoke(
        app, ["--tenant", "acme", "--team", "platform", "runs", "list"]
    )

    assert result.exit_code == 0, result.output
    assert captured["headers"]["x-hiveplane-tenant"] == "acme"
    assert captured["headers"]["x-hiveplane-team"] == "platform"


def test_register_sends_tenant_header(monkeypatch: Any, tmp_path: Path) -> None:
    captured = _capture_request(monkeypatch)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        """
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata: {name: repo-agent, owner: platform-team}
spec:
  runtime: {adapter: raw-worker, entrypoint: "examples.worker:run"}
  budget: {per_run_usd: 0.5, per_day_usd: 5.0}
  model: {strategy: tiered, identity: {provider: openai, family: gpt-4o, version: "2024-08-06"}}
  certification: {benchmark_corpus: "corpora/repo-agent/v1", status: uncertified}
"""
    )

    result = runner.invoke(app, ["--tenant", "acme", "register", str(manifest)])

    assert result.exit_code == 0, result.output
    assert captured["headers"]["x-hiveplane-tenant"] == "acme"
    assert captured["headers"]["content-type"] == "application/json"


def test_no_flags_send_no_tenant_header(monkeypatch: Any) -> None:
    captured = _capture_request(monkeypatch)

    result = runner.invoke(app, ["runs", "list"])

    assert result.exit_code == 0, result.output
    assert "x-hiveplane-tenant" not in captured["headers"]
    assert "x-hiveplane-team" not in captured["headers"]


def test_forbidden_response_is_a_clear_cli_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (403, "tenant out of scope"),
    )

    result = runner.invoke(app, ["--tenant", "beta", "runs", "list"])

    assert result.exit_code == 1
    assert "403" in result.output
    assert "Traceback" not in result.output
