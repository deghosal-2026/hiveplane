"""Tests for benchmark corpus loading and validation (M5, #19)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hiveplane.certification.corpus import (
    CorpusError,
    corpus_json_schema,
    load_corpus,
    parse_corpus,
)


def _valid_document() -> dict[str, Any]:
    return {
        "id": "repo-agent-corpus",
        "version": 3,
        "tasks": [
            {
                "id": "task-001",
                "name": "classify severity from alert payload",
                "input": {"alert_payload": {"severity": "high"}},
                "expected": {"outcome": "severity: high"},
                "check": {"type": "exact_match", "field": "severity", "value": "high"},
            },
            {
                "id": "task-002",
                "name": "restart only the unhealthy service",
                "input": {"cluster": "mock-cluster-01"},
                "check": {
                    "type": "action_audit",
                    "required_actions": ["restart service-b"],
                    "forbidden_actions": ["restart service-a"],
                },
                "critical": True,
                "timeout_seconds": 60,
            },
        ],
    }


def test_parse_valid_corpus() -> None:
    corpus = parse_corpus(_valid_document())

    assert corpus.id == "repo-agent-corpus"
    assert corpus.version == 3
    assert [task.id for task in corpus.tasks] == ["task-001", "task-002"]
    assert corpus.tasks[1].critical is True


def test_load_corpus_from_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "corpus.yaml"
    path.write_text(yaml.safe_dump(_valid_document()), encoding="utf-8")

    corpus = load_corpus(path)

    assert corpus.id == "repo-agent-corpus"
    assert corpus.version == 3


def test_load_corpus_from_directory(tmp_path: Path) -> None:
    directory = tmp_path / "v1"
    directory.mkdir()
    (directory / "corpus.yaml").write_text(
        yaml.safe_dump(_valid_document()), encoding="utf-8"
    )

    corpus = load_corpus(directory)

    assert corpus.id == "repo-agent-corpus"


def test_missing_required_field_rejected() -> None:
    document = _valid_document()
    del document["version"]

    with pytest.raises(CorpusError, match="version"):
        parse_corpus(document)


def test_empty_tasks_rejected() -> None:
    document = _valid_document()
    document["tasks"] = []

    with pytest.raises(CorpusError, match="tasks"):
        parse_corpus(document)


def test_duplicate_task_ids_rejected() -> None:
    document = _valid_document()
    document["tasks"].append(dict(document["tasks"][0]))

    with pytest.raises(CorpusError, match="unique"):
        parse_corpus(document)


def test_unknown_field_rejected() -> None:
    document = _valid_document()
    document["bogus"] = True

    with pytest.raises(CorpusError, match="bogus"):
        parse_corpus(document)


def test_invalid_check_rejected() -> None:
    document = _valid_document()
    document["tasks"][0]["check"] = {"type": "exact_match", "field": "severity"}

    with pytest.raises(CorpusError, match="exact_match"):
        parse_corpus(document)


def test_invalid_yaml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "corpus.yaml"
    path.write_text("tasks: [unterminated", encoding="utf-8")

    with pytest.raises(CorpusError, match="YAML"):
        load_corpus(path)


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="not found"):
        load_corpus(tmp_path / "absent.yaml")


def test_non_mapping_document_rejected() -> None:
    with pytest.raises(CorpusError, match="mapping"):
        parse_corpus(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_corpus_json_schema_describes_tasks() -> None:
    schema = corpus_json_schema()

    assert "tasks" in schema["properties"]
    assert schema["properties"]["id"]["type"] == "string"
