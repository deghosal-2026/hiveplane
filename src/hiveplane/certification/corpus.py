"""Benchmark corpus loading and validation (M5, #19).

A corpus is a versioned set of benchmark tasks. The canonical on-disk form is a
``corpus.yaml`` file whose document is a :class:`BenchmarkCorpus` (``id``,
``version``, ``tasks``). Passing a directory loads ``corpus.yaml`` inside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hiveplane.certification.errors import CorpusError
from hiveplane.certification.models import BenchmarkCorpus

__all__ = [
    "CORPUS_FILENAME",
    "CorpusError",
    "corpus_json_schema",
    "load_corpus",
    "parse_corpus",
]

#: Filename loaded when a corpus path is a directory.
CORPUS_FILENAME = "corpus.yaml"


def parse_corpus(data: Mapping[str, Any]) -> BenchmarkCorpus:
    """Validate an already-decoded corpus mapping into a :class:`BenchmarkCorpus`.

    Raises:
        CorpusError: when the corpus is structurally invalid.
    """
    if not isinstance(data, Mapping):
        raise CorpusError("corpus must be a mapping at the top level")
    try:
        return BenchmarkCorpus.model_validate(dict(data))
    except ValidationError as exc:
        raise CorpusError(f"corpus is invalid:\n{_format_errors(exc)}") from exc


def load_corpus(path: str | Path) -> BenchmarkCorpus:
    """Read, parse, and strictly validate a corpus file or directory.

    Raises:
        CorpusError: when the path is missing, the YAML cannot be parsed, or the
            corpus is invalid.
    """
    corpus_path = _resolve_path(path)
    if not corpus_path.exists():
        raise CorpusError(f"corpus file not found: {corpus_path}")
    text = corpus_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CorpusError(f"failed to parse corpus YAML: {exc}") from exc
    return parse_corpus(data)


def corpus_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for the on-disk benchmark corpus document."""
    return BenchmarkCorpus.model_json_schema(by_alias=True)


def _resolve_path(path: str | Path) -> Path:
    resolved = Path(path)
    if resolved.is_dir():
        return resolved / CORPUS_FILENAME
    return resolved


def _format_errors(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
