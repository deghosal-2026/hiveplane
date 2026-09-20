"""AutoGen adapter: invoke an AutoGen agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built agent instance), calls it to obtain an ``AssistantAgent``-like
object, and invokes it with ``agent.run(task=...)`` (async). The returned
``TaskResult`` is converted to a standardized run envelope with trajectory steps
(tool calls, tool results, and the final response).

Cost is extracted from the ``models_usage`` attributes on the result messages
(``RequestUsage`` with ``prompt_tokens``/``completion_tokens``).

Exports:
    AutoGenAdapter: Adapter for AutoGen-based agents.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

from evalforge.adapters._model_patch import apply_model_patch
from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class AutoGenAdapter(Adapter):
    """Invoke an AutoGen agent via Python import.

    Attributes:
        name: Identifier "autogen".
    """

    name = "autogen"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        apply_model_patch()
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("autogen adapter requires `module` in config")

        function_name = config.get("function", "build_agent")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is an autogen agent, install with: "
                "pip install evalforge[autogen]"
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
                agent = callable_builder(payload) if required else callable_builder()
            else:
                agent = builder
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not hasattr(agent, "run"):
            raise AdapterError("build_agent must return an object with a .run() method")

        user_input = payload.get("input", "")

        try:
            result = agent.run(task=user_input)
            if inspect.iscoroutine(result):
                result = asyncio.run(result)
        except Exception as exc:
            raise AdapterError(f"agent run failed: {exc}") from exc

        messages = getattr(result, "messages", None) or []
        steps = _extract_trajectory(messages)
        final_content, structured = _extract_output(messages, steps)

        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": structured},
            "trajectory": {"steps": steps},
            "cost": _extract_cost(messages),
            "error": None,
        }


def _extract_trajectory(messages: list[Any]) -> list[dict[str, Any]]:
    """Extract trajectory steps from AutoGen ``TaskResult.messages``.

    ``ToolCallSummaryMessage`` items contribute ``tool_call`` (from
    ``tool_calls``) and ``tool_result`` (from ``results``) steps.
    ``ToolCallRequestEvent`` / ``ToolCallExecutionEvent`` content lists are
    also walked. The last ``TextMessage`` (or ``ToolCallSummaryMessage``
    when no text messages follow tool calls) becomes a ``response`` step.
    """
    steps: list[dict[str, Any]] = []
    last_tool_summary_index: int | None = None
    text_indices = [
        i for i, m in enumerate(messages)
        if getattr(m, "type", "") == "TextMessage" and not (getattr(m, "tool_calls", None) or [])
    ]
    final_text_index = text_indices[-1] if text_indices else None

    for idx, message in enumerate(messages):
        msg_type = getattr(message, "type", "")
        calls = getattr(message, "tool_calls", None) or []
        results = getattr(message, "results", None) or []

        # Walk structured tool_calls/results on ToolCallSummaryMessage
        for i, call in enumerate(calls):
            name = getattr(call, "name", "") or ""
            args_raw = getattr(call, "arguments", None)
            args = _parse_jsonish(args_raw) if isinstance(args_raw, str) else args_raw
            steps.append({
                "type": "tool_call",
                "tool": name,
                "args": args if isinstance(args, dict) else {},
                "duration_ms": None,
            })
            result_content = (
                results[i].content if i < len(results) else ""
            )
            steps.append({
                "type": "tool_result",
                "tool": name,
                "result": (
                    _parse_jsonish(result_content)
                    if isinstance(result_content, str)
                    else result_content
                ),
                "duration_ms": None,
            })

        # Also walk content lists on events (FunctionCall, FunctionExecutionResult)
        content_list = getattr(message, "content", None)
        if isinstance(content_list, list) and not calls:
            for item in content_list:
                if hasattr(item, "call_id"):
                    steps.append({
                        "type": "tool_result",
                        "tool": getattr(item, "name", ""),
                        "result": getattr(item, "content", ""),
                        "duration_ms": None,
                    })
                elif hasattr(item, "name"):
                    args_raw = getattr(item, "arguments", None)
                    args = _parse_jsonish(args_raw) if isinstance(args_raw, str) else args_raw
                    steps.append({
                        "type": "tool_call",
                        "tool": getattr(item, "name", ""),
                        "args": args if isinstance(args, dict) else {},
                        "duration_ms": None,
                    })

        if msg_type == "TextMessage" and not calls and idx == final_text_index:
            steps.append({
                "type": "response",
                "content": getattr(message, "content", "") or "",
                "duration_ms": None,
            })
        if msg_type == "ToolCallSummaryMessage":
            last_tool_summary_index = idx

    if final_text_index is None and last_tool_summary_index is not None:
        msg = messages[last_tool_summary_index]
        content = getattr(msg, "content", "") or ""
        if content:
            steps.append({"type": "response", "content": content, "duration_ms": None})

    return steps


def _extract_output(messages: list[Any], steps: list[dict[str, Any]]) -> tuple[str | None, Any]:
    """Extract the final output and structured data from AutoGen messages."""
    final_text: str | None = None
    structured: Any = None
    for message in reversed(messages):
        mtype = getattr(message, "type", "")
        if mtype in ("TextMessage", "ToolCallSummaryMessage"):
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                final_text = content
                parsed = _parse_jsonish(content)
                if isinstance(parsed, dict):
                    structured = parsed
                break
    if final_text is None:
        for step in reversed(steps):
            if step.get("type") == "response" and step.get("content"):
                final_text = step["content"]
                break
    return final_text, structured


def _extract_cost(messages: list[Any]) -> dict[str, Any] | None:
    """Extract usage/cost by summing ``models_usage`` across messages."""
    prompt = 0
    completion = 0
    found = False
    for message in messages:
        usage = getattr(message, "models_usage", None)
        if usage is None:
            continue
        found = True
        prompt += getattr(usage, "prompt_tokens", 0) or 0
        completion += getattr(usage, "completion_tokens", 0) or 0
    if not found:
        return None
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": 0.0,
    }


def _parse_jsonish(value: str) -> Any:
    """Parse a JSON string, returning the original value on failure."""
    import json
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
