"""JSON-schema validation for MCP tool payloads (#601).

Validates inbound payloads *before* any store/extraction work so malformed
or abusive inputs cannot corrupt pipelines. No external dependency: the
schemas are enforced by a small explicit validator.
"""

from __future__ import annotations

from typing import Any


class McpValidationError(Exception):
    """Raised on schema mismatch (maps to 400 with structured details)."""

    status = 400

    def __init__(self, errors: list[str]) -> None:
        """Store the structured error list."""
        super().__init__("; ".join(errors))
        self.errors = errors


REPORT_FAILURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["trajectory"],
    "properties": {
        "trajectory": {"type": ["object", "string"]},
        "error": {"type": "string"},
        "metadata": {"type": "object"},
        "tags": {"type": "array"},
        "quality_label": {
            "type": "string",
            "enum": ["clear", "ambiguous", "noisy", "invalid"],
        },
    },
}


def _check_type(value: object, expected: str | list[str]) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    mapping: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    for name in names:
        target = mapping.get(name)
        if target is None:
            continue
        if isinstance(target, tuple):
            if isinstance(value, target) and not isinstance(value, bool):
                return True
        elif isinstance(value, target):
            return True
    return False


def validate_payload(data: object, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate *data* against *schema* (default: report_failure).

    Returns the data unchanged when valid; raises McpValidationError with
    structured details otherwise.
    """
    schema = schema or REPORT_FAILURE_SCHEMA
    errors: list[str] = []
    if schema.get("type") == "object" and not isinstance(data, dict):
        raise McpValidationError(["payload must be a JSON object"])
    if not isinstance(data, dict):
        raise McpValidationError(["payload must be a JSON object"])
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"missing required field: {field}")
    properties = schema.get("properties", {})
    for field, rules in properties.items():
        if field not in data:
            continue
        if "type" in rules and not _check_type(data[field], rules["type"]):
            errors.append(f"field {field!r} must be {rules['type']}")
        if "enum" in rules and data[field] not in rules["enum"]:
            errors.append(f"field {field!r} must be one of {rules['enum']}")
    if errors:
        raise McpValidationError(errors)
    return data


def validate_report_failure(trajectory_json: str) -> dict[str, Any]:
    """Parse + validate a report_failure JSON string. Returns the payload."""
    import json

    try:
        data = json.loads(trajectory_json)
    except json.JSONDecodeError as exc:
        raise McpValidationError([f"invalid JSON: {exc}"]) from None
    # Accept a bare trajectory object (legacy stdio shape) by wrapping it.
    if isinstance(data, dict) and "trajectory" not in data and "steps" in data:
        data = {"trajectory": data}
    return validate_payload(data)
