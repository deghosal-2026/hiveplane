"""Tests for typed, length-limited task templating (M27-06)."""

from __future__ import annotations

from typing import Any

import pytest

from hiveplane.triggers.templating import (
    TemplateError,
    render_task,
    render_template,
    validate_template,
)

_EVENT: dict[str, Any] = {
    "repository": {"full_name": "acme/fleet", "private": False},
    "number": 42,
    "labels": ["bug", "p1"],
    "comment": {"body": "/hiveplane analyze"},
}


def test_validate_accepts_declared_paths() -> None:
    validate_template("{{ event.repository.full_name }}")
    validate_template("PR #{{ event.number }} in {{ event.repository.full_name }}")


@pytest.mark.parametrize(
    "template",
    [
        "{{ event.repository.full_name + 1 }}",
        "{{ event.__class__ }}",
        "{{ env.SECRET }}",
        "{{ event.number",
        "{{ }}",
        "{{ event }}",
        "{{ event. }}",
        "{{ event.number; rm -rf / }}",
    ],
)
def test_validate_rejects_unsafe_templates(template: str) -> None:
    with pytest.raises(TemplateError):
        validate_template(template)


def test_render_preserves_type_for_whole_placeholder() -> None:
    assert render_template("{{ event.number }}", _EVENT) == 42
    assert render_template("{{ event.labels }}", _EVENT) == ["bug", "p1"]
    assert render_template("{{ event.repository.private }}", _EVENT) is False


def test_render_interpolates_scalars_into_strings() -> None:
    rendered = render_template(
        "PR #{{ event.number }} in {{ event.repository.full_name }}", _EVENT
    )
    assert rendered == "PR #42 in acme/fleet"


def test_render_rejects_missing_path() -> None:
    with pytest.raises(TemplateError):
        render_template("{{ event.missing.path }}", _EVENT)


def test_render_rejects_container_in_string_context() -> None:
    with pytest.raises(TemplateError):
        render_template("labels: {{ event.labels }}", _EVENT)


def test_render_task_renders_each_field() -> None:
    task = render_task(
        {"repo": "{{ event.repository.full_name }}", "pr": "{{ event.number }}"},
        _EVENT,
    )
    assert task == {"repo": "acme/fleet", "pr": 42}


def test_render_task_enforces_field_and_total_limits() -> None:
    with pytest.raises(TemplateError):
        render_task({"repo": "{{ event.repository.full_name }}"}, _EVENT, max_field_bytes=3)
    with pytest.raises(TemplateError):
        render_task(
            {"a": "{{ event.repository.full_name }}", "b": "{{ event.comment.body }}"},
            _EVENT,
            max_total_bytes=5,
        )
