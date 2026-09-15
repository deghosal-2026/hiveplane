"""Tests for the hiveplane CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from hiveplane.cli import app

runner = CliRunner()


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
