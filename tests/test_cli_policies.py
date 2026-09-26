"""Tests for the policy pack CLI (M40-07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


def _pack_document(name: str = "strict") -> dict[str, Any]:
    return {
        "apiVersion": "hiveplane/v1",
        "kind": "PolicyPack",
        "metadata": {"name": name, "team": "platform", "version": "1"},
        "spec": {"overrides": []},
    }


def _write(tmp_path: Path, name: str = "strict") -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(_pack_document(name)), encoding="utf-8")
    return path


def test_policies_lint_valid(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policies", "lint", str(_write(tmp_path))])

    assert result.exit_code == 0
    assert "valid" in result.output


def test_policies_lint_rejects_unknown_parent(tmp_path: Path) -> None:
    document = _pack_document()
    document["spec"]["inherits"] = ["missing"]
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = runner.invoke(app, ["policies", "lint", str(path)])

    assert result.exit_code == 1


def test_policies_publish_posts_the_pack(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        captured["payload"] = payload
        return 201, json.dumps(_pack_document())

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["policies", "publish", str(_write(tmp_path))])

    assert result.exit_code == 0
    assert captured["url"].endswith("/policy-packs")
    assert captured["payload"]["kind"] == "PolicyPack"


def test_policies_apply_pins_to_team(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        captured["payload"] = payload
        return 200, json.dumps([_pack_document()])

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["policies", "apply", "strict", "--team", "payments"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/policy-packs/strict/apply")
    assert captured["payload"] == {"team": "payments"}
