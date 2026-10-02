"""Pipeline handoffs: reference resolution and schema validation (M29-03).

Handoff mappings use a restricted ``${node.output.path}`` language — no arbitrary
code. Values are resolved against a :class:`PipelineContext` and validated against
the producer's output schema and the consumer's input schema at both boundaries,
so a mismatch fails the node before any partial input reaches an agent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

_REF = re.compile(r"\$\{([^}]+)\}")
_WHOLE = re.compile(r"\A\$\{\s*([^{}]+?)\s*\}\Z")


class HandoffError(ValueError):
    """Raised when a handoff reference cannot resolve or a schema does not match."""


class SchemaType(StrEnum):
    """The JSON Schema types supported for handoff validation."""

    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    NULL = "null"


class JsonSchema(BaseModel):
    """A minimal JSON Schema subset used to validate handoff payloads."""

    model_config = ConfigDict(extra="allow")

    type: SchemaType | None = None
    required: list[str] = []
    properties: dict[str, JsonSchema] = {}
    items: JsonSchema | None = None


class PipelineContext(BaseModel):
    """Resolved node outputs and fan-out variables for one pipeline run."""

    model_config = ConfigDict(extra="forbid")

    outputs: dict[str, dict[str, JsonValue]] = {}
    variables: dict[str, JsonValue] = {}

    def lookup(self, expression: str) -> JsonValue:
        """Resolve a ``${...}`` expression body, raising :class:`HandoffError`."""
        segments = expression.split(".")
        root = segments[0]
        if root in self.variables:
            current: JsonValue = self.variables[root]
            for segment in segments[1:]:
                if not isinstance(current, Mapping) or segment not in current:
                    raise HandoffError(f"pipeline context has no {expression!r}")
                current = current[segment]
            return current
        if root in self.outputs:
            current = self.outputs[root]
            for segment in segments[1:]:
                if not isinstance(current, Mapping) or segment not in current:
                    raise HandoffError(f"pipeline context has no {expression!r}")
                current = current[segment]
            return current
        raise HandoffError(f"pipeline context has no {expression!r}")


def render_ref(template: str, context: PipelineContext) -> JsonValue:
    """Render one input template, preserving type for a whole placeholder."""
    whole = _WHOLE.match(template.strip())
    if whole is not None:
        return context.lookup(whole.group(1).strip())
    return _REF.sub(
        lambda match: _stringify(context.lookup(match.group(1).strip())), template
    )


def map_inputs(
    mapping: Mapping[str, str], context: PipelineContext
) -> dict[str, JsonValue]:
    """Render every input field of a node."""
    return {field: render_ref(template, context) for field, template in mapping.items()}


def validate_schema(value: JsonValue, schema: Mapping[str, JsonValue] | JsonSchema) -> None:
    """Validate ``value`` against a JSON Schema subset, raising :class:`HandoffError`."""
    parsed = schema if isinstance(schema, JsonSchema) else JsonSchema.model_validate(schema)
    _validate(value, parsed, "$")


def validate_boundary(
    value: JsonValue,
    schema: Mapping[str, JsonValue] | JsonSchema | None,
    *,
    node: str,
    boundary: str,
) -> None:
    """Validate a handoff at a producer/consumer boundary, naming the node."""
    if schema is None:
        return
    try:
        validate_schema(value, schema)
    except (HandoffError, ValidationError) as exc:
        raise HandoffError(
            f"{boundary} schema mismatch at node {node!r}: {exc}"
        ) from exc


def _validate(value: JsonValue, schema: JsonSchema, path: str) -> None:
    if schema.type is None:
        return
    if schema.type is SchemaType.OBJECT:
        if not isinstance(value, dict):
            raise HandoffError(f"{path}: expected object")
        for key in schema.required:
            if key not in value:
                raise HandoffError(f"{path}: missing required field {key!r}")
        for key, subschema in schema.properties.items():
            if key in value:
                _validate(value[key], subschema, f"{path}/{key}")
        return
    if schema.type is SchemaType.ARRAY:
        if not isinstance(value, list):
            raise HandoffError(f"{path}: expected array")
        if schema.items is not None:
            for index, item in enumerate(value):
                _validate(item, schema.items, f"{path}/{index}")
        return
    if schema.type is SchemaType.STRING and not isinstance(value, str):
        raise HandoffError(f"{path}: expected string")
    if schema.type is SchemaType.NUMBER and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise HandoffError(f"{path}: expected number")
    if schema.type is SchemaType.INTEGER and (
        isinstance(value, bool) or not isinstance(value, int)
    ):
        raise HandoffError(f"{path}: expected integer")
    if schema.type is SchemaType.BOOLEAN and not isinstance(value, bool):
        raise HandoffError(f"{path}: expected boolean")
    if schema.type is SchemaType.NULL and value is not None:
        raise HandoffError(f"{path}: expected null")


def _stringify(value: JsonValue) -> str:
    if isinstance(value, (dict, list)):
        raise HandoffError("cannot embed a container value in a string template")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
