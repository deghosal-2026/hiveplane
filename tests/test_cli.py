"""Tests for the hiveplane CLI."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any, Literal

import yaml
from typer.testing import CliRunner

from hiveplane.cli import _post_workload, app

runner = CliRunner()

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"
REPO_MANIFEST = EXAMPLES_DIR / "repo-agent.yaml"


def _manifest() -> dict[str, Any]:
    return {
        "apiVersion": "hiveplane/v1",
        "kind": "AgentWorkload",
        "metadata": {"name": "repo-agent", "owner": "platform-team"},
        "spec": {
            "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
            "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
            "model": {
                "strategy": "tiered",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
            "certification": {
                "benchmark_corpus": "corpora/repo-agent/v1",
                "status": "uncertified",
            },
        },
    }


def test_validate_valid_manifest(tmp_path: Path) -> None:
    path = tmp_path / "repo-agent.yaml"
    path.write_text(yaml.safe_dump(_manifest()))

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_validate_invalid_manifest_exits_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump({"kind": "AgentWorkload"}))

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "nope.yaml")])

    assert result.exit_code != 0


def test_register_dry_run_reports_enforcement() -> None:
    result = runner.invoke(app, ["register", str(REPO_MANIFEST), "--dry-run"])

    assert result.exit_code == 0
    assert "production_admitted" in result.output


def test_register_posts_manifest_to_api(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(api_url: str, payload: dict[str, Any]) -> tuple[int, str]:
        captured["api_url"] = api_url
        captured["payload"] = payload
        return 201, "{}"

    monkeypatch.setattr("hiveplane.cli._post_workload", fake_post)

    result = runner.invoke(
        app, ["register", str(REPO_MANIFEST), "--api-url", "http://api.test"]
    )

    assert result.exit_code == 0
    assert captured["api_url"] == "http://api.test"
    assert captured["payload"]["metadata"]["name"] == "repo-agent"


def test_register_reports_api_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._post_workload", lambda api_url, payload: (409, "conflict")
    )

    result = runner.invoke(app, ["register", str(REPO_MANIFEST)])

    assert result.exit_code == 1
    assert "409" in result.output


def test_register_invalid_manifest_exits_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"kind": "AgentWorkload"}))

    result = runner.invoke(app, ["register", str(path)])

    assert result.exit_code == 1


def test_post_workload_handles_success(monkeypatch: Any) -> None:
    class FakeResponse:
        status = 201

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request: FakeResponse())

    code, body = _post_workload("http://api", {"a": 1})

    assert code == 201
    assert body == '{"ok": true}'


def test_post_workload_handles_http_error(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", Message(), io.BytesIO(b"nope")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    code, body = _post_workload("http://api", {})

    assert code == 409
    assert body == "nope"


def test_post_workload_handles_url_error(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any) -> Any:
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    code, _ = _post_workload("http://api", {})

    assert code == 0
