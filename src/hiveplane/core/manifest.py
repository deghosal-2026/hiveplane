"""Manifest parsing, validation, and JSON Schema export (D1, DD-01)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hiveplane.core.workload import AgentWorkload


class ManifestError(Exception):
    """Raised when a manifest cannot be parsed or fails strict validation."""


def parse_manifest(data: Mapping[str, Any]) -> AgentWorkload:
    """Validate an already-decoded manifest mapping into an AgentWorkload.

    Raises:
        pydantic.ValidationError: when the manifest is structurally invalid.
    """
    return AgentWorkload.model_validate(dict(data))


def load_manifest(path: str | Path) -> AgentWorkload:
    """Read, parse, and strictly validate a YAML manifest file.

    Raises:
        ManifestError: when the YAML cannot be parsed or the manifest is invalid.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"failed to parse manifest YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ManifestError("manifest must be a YAML mapping at the top level")
    try:
        return AgentWorkload.model_validate(dict(data))
    except ValidationError as exc:
        raise ManifestError(f"manifest is invalid:\n{_format_errors(exc)}") from exc


def manifest_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for the AgentWorkload manifest."""
    return AgentWorkload.model_json_schema(by_alias=True)


def _format_errors(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
