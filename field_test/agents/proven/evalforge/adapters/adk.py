"""Google ADK adapter: invoke a Google ADK agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built runner instance), calls it to obtain a ``Runner``-like object,
and invokes it with ``runner.run(user_id=..., session_id=..., new_message=...)``
(a sync generator yielding ``Event`` objects). The events are converted to a
standardized run envelope with trajectory steps (tool calls, tool results, and
the final response).

Trajectory is extracted from each event's ``get_function_calls()`` (→
``tool_call``) and ``get_function_responses()`` (→ ``tool_result``); the final
event's text content becomes a ``response`` step.

Exports:
    ADKAdapter: Adapter for Google ADK-based agents.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from evalforge.adapters._model_patch import apply_model_patch
from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class ADKAdapter(Adapter):
    """Invoke a Google ADK agent via Python import.

    Attributes:
        name: Identifier "adk".
    """

    name = "adk"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        apply_model_patch()
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("adk adapter requires `module` in config")

        function_name = config.get("function", "build_agent")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is an adk agent, install with: "
                "pip install evalforge[adk]"
            ) from exc

        try:
            builder: Any = getattr(mod, function_name)
        except AttributeError as exc:
            raise AdapterError(
                f"module '{module_name}' has no function '{function_name}'"
            ) from exc

        try:
            if inspect.isfunction(builder) or inspect.ismethod(builder) or inspect.isclass(builder):
                sig = inspect.signature(builder)
                required = [
                    p for p in sig.parameters.values()
                    if p.default is inspect._empty
                    and p.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                callable_builder: Any = builder
                runner = callable_builder(payload) if required else callable_builder()
            else:
                runner = builder
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not hasattr(runner, "run"):
            raise AdapterError("build_agent must return an object with a .run() method")

        user_input = payload.get("input", "")
        run_id = config.get("run_id", "evalforge")

        try:
            events = runner.run(user_id="evalforge", session_id=run_id, new_message=user_input)
            steps, final_content, structured = _walk_events(events)
        except Exception as exc:
            raise AdapterError(f"adk agent failed: {exc}") from exc

        if final_content and not any(s.get("type") == "response" for s in steps):
            steps.append({
                "type": "response",
                "content": final_content,
                "duration_ms": None,
            })

        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": structured},
            "trajectory": {"steps": steps},
            "cost": None,
            "error": None,
        }


def _walk_events(events: Any) -> tuple[list[dict[str, Any]], str | None, Any]:
    """Walk ADK events, returning (steps, final_content, structured)."""
    steps: list[dict[str, Any]] = []
    final_text: str | None = None
    for event in events:
        for call in event.get_function_calls():
            steps.append({
                "type": "tool_call",
                "tool": getattr(call, "name", "") or "",
                "args": getattr(call, "args", {}) or {},
                "duration_ms": None,
            })
        for response in event.get_function_responses():
            steps.append({
                "type": "tool_result",
                "tool": getattr(response, "name", "") or "",
                "result": getattr(response, "response", None),
                "duration_ms": None,
            })
        if hasattr(event, "is_final_response") and event.is_final_response():
            text = _extract_event_text(event)
            if text:
                final_text = text
    structured = None
    if final_text:
        parsed = _parse_jsonish(final_text)
        if isinstance(parsed, dict):
            structured = parsed
    return steps, final_text, structured


def _extract_event_text(event: Any) -> str | None:
    """Extract concatenated text parts from an event's content."""
    content = getattr(event, "content", None)
    if content is None:
        return None
    parts = getattr(content, "parts", None) or []
    texts = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return "".join(texts) if texts else None


def _parse_jsonish(value: str) -> Any:
    """Parse a JSON string, returning the original value on failure."""
    import json
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
