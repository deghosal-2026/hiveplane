"""Typed, length-limited, injection-safe task templating (M27-06, D23).

Templates are value-only substitutions over declared ``event.*`` paths. There is
no expression language: arithmetic, function calls, attribute traversal outside
declared paths, and shell interpolation are rejected at validation. A whole
placeholder preserves the JSON value's type; a placeholder embedded in a string
must render a scalar. Rendered fields are byte-limited so a hostile payload
cannot blow up agent context.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from pydantic import JsonValue

_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_WHOLE = re.compile(r"\A\{\{\s*([^{}]*?)\s*\}\}\Z")


class TemplateError(ValueError):
    """Raised when a template is unsafe, unresolvable, or over its length limit."""


def validate_template(template: str) -> None:
    """Validate a template string, raising :class:`TemplateError` when unsafe."""
    stripped = template.strip()
    if not stripped:
        return
    _reject_unbalanced(template)
    for path in _paths(template):
        _validate_path(path)


def render_template(template: str, event: Mapping[str, JsonValue]) -> JsonValue:
    """Render one template against ``event``, preserving type for whole placeholders."""
    validate_template(template)
    whole = _WHOLE.match(template.strip())
    if whole is not None:
        return _resolve(_clean_path(whole.group(1)), event)
    rendered = _PLACEHOLDER.sub(
        lambda match: _stringify(_resolve(_clean_path(match.group(1)), event)), template
    )
    return rendered


def render_task(
    mapping: Mapping[str, str],
    event: Mapping[str, JsonValue],
    *,
    max_field_bytes: int = 8192,
    max_total_bytes: int = 65536,
) -> dict[str, JsonValue]:
    """Render a task template mapping, enforcing per-field and total byte limits."""
    task: dict[str, JsonValue] = {}
    total = 0
    for field, template in mapping.items():
        value = render_template(template, event)
        size = _size(value)
        if size > max_field_bytes:
            raise TemplateError(
                f"rendered field {field!r} is {size} bytes, over {max_field_bytes}"
            )
        total += size
        if total > max_total_bytes:
            raise TemplateError(f"rendered task is over {max_total_bytes} bytes")
        task[field] = value
    return task


def _paths(template: str) -> list[str]:
    if "{{" in template and _WHOLE.search(template) is None and not _PLACEHOLDER.search(template):
        raise TemplateError(f"unbalanced or malformed placeholder in {template!r}")
    return [match.group(1).strip() for match in _PLACEHOLDER.finditer(template)]


def _reject_unbalanced(template: str) -> None:
    if template.count("{{") != template.count("}}"):
        raise TemplateError(f"unbalanced placeholder braces in {template!r}")


def _clean_path(path: str) -> list[str]:
    segments = path.strip().split(".")
    if segments[0] != "event":
        raise TemplateError(f"template paths must start with 'event': {path!r}")
    return [segment.strip() for segment in segments[1:]]


def _validate_path(path: str) -> None:
    segments = path.split(".")
    if len(segments) < 2 or segments[0] != "event":
        raise TemplateError(f"template path must be 'event.<field>': {path!r}")
    for segment in segments[1:]:
        if not _IDENT.match(segment):
            raise TemplateError(f"invalid template path segment {segment!r} in {path!r}")


def _resolve(segments: list[str], event: Mapping[str, JsonValue]) -> JsonValue:
    current: JsonValue = dict(event)
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            raise TemplateError(f"event has no field {'.'.join(segments)!r}")
        current = current[segment]
    return current


def _stringify(value: JsonValue) -> str:
    if isinstance(value, (dict, list)):
        raise TemplateError("cannot embed a container value in a string template")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _size(value: JsonValue) -> int:
    encoded = json.dumps(value, separators=(",", ":"), default=str)
    return len(encoded.encode("utf-8"))
