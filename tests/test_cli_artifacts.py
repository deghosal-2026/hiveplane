"""CLI tests for artifacts and export/import (M54)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _json(payload: Any) -> str:
    return json.dumps(payload)


def test_artifacts_list(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(url=url)
        return 200, _json(
            [
                {
                    "artifact_id": "art-1",
                    "run_id": "run-1",
                    "size_bytes": 5,
                    "content_hash": "sha256:abc",
                }
            ]
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["artifacts", "list", "--run-id", "run-1"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/artifacts?run_id=run-1")
    assert "art-1" in result.output


def test_artifacts_put_reads_file_and_posts_base64(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(url=url, payload=payload)
        return 200, _json({"artifact_id": "art-1", "content_hash": "sha256:abc"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    source = tmp_path / "report.txt"
    source.write_bytes(b"hello")

    result = runner.invoke(
        app, ["artifacts", "put", "--run-id", "run-1", str(source)]
    )

    assert result.exit_code == 0
    assert captured["url"].endswith("/artifacts")
    assert captured["payload"]["filename"] == "report.txt"
    assert base64.b64decode(captured["payload"]["content"]) == b"hello"


def test_artifacts_content_to_file(monkeypatch: Any, tmp_path: Path) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        return 200, "payload-bytes"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    out = tmp_path / "out.bin"

    result = runner.invoke(
        app, ["artifacts", "content", "art-1", "--out", str(out)]
    )

    assert result.exit_code == 0
    assert out.read_bytes() == b"payload-bytes"


def test_artifacts_purge(monkeypatch: Any) -> None:
    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        assert url.endswith("/retention/purge")
        return 200, _json({"purged": ["a"], "skipped_legal_hold": [], "completed_at": "t"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["artifacts", "purge"])

    assert result.exit_code == 0
    assert "purged=1" in result.output


def test_export_posts_bundle(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(url=url, payload=payload)
        return 200, _json({"api_version": "hiveplane/export/v1", "kind": "FleetBundle"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    manifest = tmp_path / "workload.yaml"
    manifest.write_text("apiVersion: hiveplane/v1\nkind: AgentWorkload\n")

    result = runner.invoke(app, ["export", "--workload", str(manifest)])

    assert result.exit_code == 0
    assert captured["url"].endswith("/export")
    assert captured["payload"]["workload"]["kind"] == "AgentWorkload"


def test_import_defaults_to_dry_run(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(url=url, payload=payload)
        return 200, _json(
            {
                "entries": [
                    {"kind": "workload", "name": "summarizer", "action": "create"}
                ],
                "dry_run": True,
            }
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"kind": "FleetBundle"}))

    result = runner.invoke(app, ["import", str(bundle)])

    assert result.exit_code == 0
    assert "dry_run=true" in captured["url"]
    assert "create  workload  summarizer" in result.output
