"""OpenAI Agents SDK adapter: invoke an OpenAI Agents agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built agent instance), calls it to obtain an ``agents.Agent``, and
invokes it with ``agents.Runner.run_sync``. The returned ``RunResult`` is
converted to a standardized run envelope with trajectory steps (tool calls,
tool results, and the final response).

Both the built agent and result are duck-typed: the builder must return an
object invokable via ``run_sync(input)`` (matching ``Runner.run_sync``), and
the result is walked via ``new_items``/``final_output``/``usage`` attributes
so the adapter works with mocked frameworks as well as the real SDK.

Usage cost is extracted from the result's ``usage`` attribute when available.

Exports:
    OpenAIAgentsAdapter: Adapter for OpenAI Agents SDK-based agents.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class OpenAIAgentsAdapter(Adapter):
    """Invoke an OpenAI Agents SDK agent via Python import.

    Imports a user module, calls its ``build_agent`` function (or uses a
    pre-built agent), and invokes it with ``run_sync``. Trajectory steps are
    extracted from the result's ``new_items``.

    Attributes:
        name: Identifier "openai-agents".
    """

    name = "openai-agents"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Import and invoke an OpenAI Agents SDK agent, returning a run envelope.

        Steps:
        1. Import the user's module and resolve the builder function.
        2. If the builder is a callable factory, call it (respecting its
           signature); otherwise treat it as a pre-built agent instance.
        3. Verify the agent is invokable via ``run_sync``.
        4. Invoke the agent with the scenario input.
        5. Extract trajectory and final content from the result items.
        6. Extract usage/cost information if available.

        Args:
            payload: The invocation payload dict with ``input`` key.
            config: Adapter configuration; requires ``module``, optionally
                ``function`` and ``model``.

        Returns:
            A run envelope dict with status, output (final + structured),
            trajectory, cost, and error fields.

        Raises:
            AdapterError: If module import fails, builder is missing, agent
                has no ``run_sync`` method, or invocation fails.
        """
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("openai-agents adapter requires `module` in config")

        function_name = config.get("function", "build_agent")
        model_override = config.get("model")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is an openai-agents agent, install with: "
                "pip install evalforge[openai-agents]"
            ) from exc

        try:
            builder: Any = getattr(mod, function_name)
        except AttributeError as exc:
            raise AdapterError(
                f"module '{module_name}' has no function '{function_name}'"
            ) from exc

        try:
            import inspect as _inspect
            kwargs: dict[str, Any] = {}
            if model_override:
                kwargs["model"] = model_override
            # Treat non-function/class attributes as prebuilt agent instances
            # rather than calling them as factories.
            if not (
                _inspect.isfunction(builder)
                or _inspect.ismethod(builder)
                or _inspect.isclass(builder)
            ):
                agent = builder
            else:
                sig = _inspect.signature(builder)
                required = [
                    p for p in sig.parameters.values()
                    if p.default is _inspect._empty
                    and p.kind in (
                        _inspect.Parameter.POSITIONAL_ONLY,
                        _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                callable_builder: Any = builder
                agent = (
                    callable_builder(**kwargs) if not required
                    else callable_builder(payload, **kwargs)
                )
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        user_input = payload.get("input", "")

        try:
            if hasattr(agent, "run_sync"):
                result = agent.run_sync(user_input)
            else:
                # Real Runner exposes run_sync(agent, input); if the built
                # object isn't directly invokable, invoke it that way.
                _agents = importlib.import_module("agents")
                result = _agents.Runner.run_sync(agent, user_input)
        except ImportError as exc:
            raise AdapterError(
                "openai-agents SDK is required to invoke a real agent; "
                "install with: pip install evalforge[openai-agents]"
            ) from exc
        except Exception as exc:
            raise AdapterError(f"agent invocation failed: {exc}") from exc

        steps = _extract_trajectory(result)
        final_content, structured = _extract_output(result)
        if final_content:
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
            "cost": _extract_cost(result),
            "error": None,
        }


def _extract_trajectory(result: Any) -> list[dict[str, Any]]:
    """Extract trajectory steps from an OpenAI Agents ``RunResult``.

    Walks ``new_items`` to identify:
    - Tool calls (``function_call`` items): tool name + parsed args.
    - Tool results (``function_call_output`` items): tool name + parsed output.
    - Final response (``message`` items / ``final_output``): response step.

    Args:
        result: The OpenAI Agents ``RunResult`` object.

    Returns:
        A list of tool_call, tool_result, and response step dicts.
    """
    steps: list[dict[str, Any]] = []
    items = getattr(result, "new_items", None) or []

    for item in items:
        item_type = getattr(item, "type", "")
        if item_type in ("function_call", "tool_call_item"):
            raw = item.raw_item if hasattr(item, "raw_item") else item
            name = (
                getattr(item, "tool_name", "")
                or getattr(raw, "name", "")
                or getattr(item, "name", "") or ""
            )
            args_raw = getattr(raw, "arguments", None) or getattr(item, "arguments", "")
            args = _parse_jsonish(args_raw) if isinstance(args_raw, str) else args_raw
            steps.append({
                "type": "tool_call",
                "tool": name,
                "args": args if isinstance(args, dict) else {},
                "duration_ms": None,
            })
        elif item_type in ("function_call_output", "tool_call_output_item"):
            raw = item.raw_item if hasattr(item, "raw_item") else item
            name = (
                getattr(item, "tool_name", "")
                or getattr(raw, "name", "")
                or getattr(item, "name", "") or ""
            )
            output_raw = getattr(item, "output", None)
            if output_raw is None:
                output_raw = getattr(raw, "output", "")
            result_ = _parse_jsonish(output_raw) if isinstance(output_raw, str) else output_raw
            steps.append({
                "type": "tool_result",
                "tool": name,
                "result": result_,
                "duration_ms": None,
            })

    return steps


def _extract_output(result: Any) -> tuple[str | None, Any]:
    """Extract the final output and structured data from a ``RunResult``.

    The final output comes from ``final_output``. When it is a dict, it is
    surfaced as both the final text (stringified) and the structured payload.
    Historically, item text may carry the final message (SDK >= 1.x),
    so the final text from the last ``message`` item is used as a fallback.

    Args:
        result: The OpenAI Agents ``RunResult`` object.

    Returns:
        A tuple of (final_text, structured). ``structured`` is a dict when
        available, else None.
    """
    final_output = getattr(result, "final_output", None)

    if isinstance(final_output, dict):
        return str(final_output), final_output
    if isinstance(final_output, str) and final_output:
        return final_output, None

    items = getattr(result, "new_items", None) or []
    message_text: str | None = None
    for item in items:
        if getattr(item, "type", "") == "message":
            text = getattr(item, "text", None) or getattr(item, "content", None)
            if isinstance(text, str) and text:
                message_text = text

    return message_text, None


def _extract_cost(result: Any) -> dict[str, Any] | None:
    """Extract usage/cost if the result has an ``usage`` attribute.

    Args:
        result: The OpenAI Agents ``RunResult`` object.

    Returns:
        A dict with ``input_tokens``, ``output_tokens``, ``total_tokens``, and
        ``cost_usd`` (currently 0), or None if usage information is unavailable.
    """
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cost_usd": 0.0,
    }


def _parse_jsonish(value: str) -> Any:
    """Parse a JSON string, returning the original value on failure.

    Args:
        value: A string that may contain JSON.

    Returns:
        The parsed JSON data, or the original string if not valid JSON.
    """
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
