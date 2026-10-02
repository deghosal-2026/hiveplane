"""CLI tests for corpus authoring and publishing (M55-01/02/03)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from hiveplane.cli import app
from hiveplane.corpus.authoring import validate_corpus

runner = CliRunner()


def test_corpus_init_scaffolds_a_valid_corpus(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["corpus", "init", str(tmp_path), "--id", "demo", "--template", "triage"],
    )

    assert result.exit_code == 0
    corpus = validate_corpus(tmp_path / "corpus.yaml")
    assert corpus.id == "demo"


def test_corpus_init_rejects_unknown_template(tmp_path: Path) -> None:
    result = runner.invoke(app, ["corpus", "init", str(tmp_path), "--template", "nope"])
    assert result.exit_code == 2


def test_corpus_validate_and_lint(tmp_path: Path) -> None:
    runner.invoke(app, ["corpus", "init", str(tmp_path), "--id", "demo"])
    path = tmp_path / "corpus.yaml"

    validated = runner.invoke(app, ["corpus", "validate", str(path)])
    assert validated.exit_code == 0

    linted = runner.invoke(app, ["corpus", "lint", str(path)])
    assert linted.exit_code == 0


def test_corpus_add_appends_a_task(tmp_path: Path) -> None:
    runner.invoke(app, ["corpus", "init", str(tmp_path), "--id", "demo"])

    result = runner.invoke(
        app,
        [
            "corpus",
            "add",
            str(tmp_path / "corpus.yaml"),
            "--task-id",
            "extra",
            "--name",
            "extra",
            "--field",
            "output",
            "--value",
            "ok",
            "--no-critical",
        ],
    )

    assert result.exit_code == 0
    assert validate_corpus(tmp_path / "corpus.yaml").tasks[-1].id == "extra"


def test_corpus_publish_posts_to_api(monkeypatch: Any, tmp_path: Path) -> None:
    runner.invoke(app, ["corpus", "init", str(tmp_path), "--id", "demo"])
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, payload: Any = None) -> tuple[int, str]:
        captured.update(url=url, payload=payload)
        return 200, json.dumps(
            {"corpus_id": "demo", "version": 1, "content_hash": "sha256:abc"}
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["corpus", "publish", str(tmp_path / "corpus.yaml")])

    assert result.exit_code == 0
    assert captured["url"].endswith("/corpora")
    assert captured["payload"]["corpus"]["id"] == "demo"


def test_init_seeds_a_valid_corpus(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0
    corpus = validate_corpus(tmp_path / "corpora" / "hello-agent" / "v1")
    assert corpus.tasks


def test_init_template_seeds_a_typed_corpus(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--template", "triage"])

    assert result.exit_code == 0
    corpus = validate_corpus(tmp_path / "corpora" / "hello-agent" / "v1")
    assert corpus.id == "hello-agent-corpus"
