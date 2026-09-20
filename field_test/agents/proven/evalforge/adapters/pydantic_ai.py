"""PydanticAI adapter: invoke a PydanticAI agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built agent instance), calls it to obtain a PydanticAI ``Agent``,
and invokes it with ``run_sync``. The agent's message parts are converted to a
standardized run envelope with trajectory steps (tool calls, tool returns,
and the final response).

Usage cost is extracted from ``Result.usage()`` when available
(pydantic-ai >= 0.0.10).

Exports:
    PydanticAIAdapter: Adapter for PydanticAI-based agents.
"""

from __future__ import annotations

import importlib
from typing import Any

from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class PydanticAIAdapter(Adapter):
    """Invoke a PydanticAI agent via Python import.

    Imports a user module, calls its ``build_agent`` function (or uses a
    pre-built agent), and invokes it with ``run_sync``. Trajectory steps are
    extracted from the agent's message parts.

    Attributes:
        name: Identifier "pydantic-ai".
    """

    name = "pydantic-ai"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Import and invoke a PydanticAI agent, returning a run envelope.

        Steps:
        1. Import the user's module and resolve the builder function/attribute.
        2. If the builder is a callable factory, call it (respecting its
           signature); otherwise treat it as a pre-built agent instance.
        3. Verify the agent has a ``run_sync`` method.
        4. Invoke the agent with the scenario input.
        5. Extract trajectory and final content from message parts.
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
            raise AdapterError("pydantic-ai adapter requires `module` in config")

        function_name = config.get("function", "build_agent")
        model_override = config.get("model")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is a pydantic-ai agent, install with: pip install evalforge[pydanticai]"
            ) from exc

        try:
            from typing import Any as _Any
            builder: _Any = getattr(mod, function_name)
        except AttributeError as exc:
            raise AdapterError(
                f"module '{module_name}' has no function '{function_name}'"
            ) from exc

        try:
            kwargs: dict[str, Any] = {}
            if model_override:
                kwargs["model"] = model_override
            builder_any: _Any = builder
            import inspect as _inspect
            # Treat non-function/class attributes as prebuilt agent instances
            # rather than calling them as factories.
            if not (
                _inspect.isfunction(builder_any)
                or _inspect.ismethod(builder_any)
                or _inspect.isclass(builder_any)
            ):
                agent = builder_any
            else:
                # If it's a callable factory, respect its signature; only pass
                # the payload when the function requires positional arguments.
                sig = _inspect.signature(builder_any)
                required = [
                    p for p in sig.parameters.values()
                    if p.default is _inspect._empty
                    and p.kind in (
                        _inspect.Parameter.POSITIONAL_ONLY,
                        _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                callable_builder: _Any = builder_any
                agent = (
                    callable_builder(**kwargs) if not required
                    else callable_builder(payload, **kwargs)
                )
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not hasattr(agent, "run_sync"):
            raise AdapterError(
                "build_agent must return an object with a .run_sync() method"
            )

        user_input = payload.get("input", "")

        try:
            if hasattr(agent, "run_sync"):
                result = agent.run_sync(user_input)
            elif hasattr(agent, "run"):
                result = agent.run(user_input)
            else:
                raise AdapterError("agent has neither run_sync nor run method")
        except Exception as exc:
            raise AdapterError(f"agent invocation failed: {exc}") from exc

        all_messages = result.all_messages() if hasattr(result, "all_messages") else []
        steps, final_content = _extract_trajectory(all_messages)
        _ensure_tool_call_steps(steps)

        structured = getattr(result, "data", None)
        if structured is not None:
            final_content = final_content or str(structured)

        structured_output = structured if isinstance(structured, dict) else None
        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": structured_output},
            "trajectory": {"steps": steps},
            "cost": _extract_cost(result),
            "error": None,
        }


def _get_part_kind(part: Any) -> str:
    """Extract the part kind string from a PydanticAI message part.

    PydanticAI >= 0.20 uses ``part_kind`` (a string literal). Older versions
    used ``kind`` (sometimes a callable). This helper handles both.
    """
    kind = getattr(part, "part_kind", None)
    if kind is None:
        kind_attr = getattr(part, "kind", "")
        if callable(kind_attr):
            try:
                kind = kind_attr()
            except Exception:
                kind = ""
        else:
            kind = kind_attr
    return str(kind) if kind else ""


def _extract_trajectory(
    all_messages: list[Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Extract trajectory steps and final content from PydanticAI message parts.

    Iterates through all messages and their ``parts`` to identify:
    - Tool calls (``tool-call`` kind)
    - Tool results (``tool-return`` kind)
    - Final response (``final`` kind)

    Args:
        all_messages: A list of PydanticAI message objects.

    Returns:
        A tuple of (steps_list, final_content_string). Steps include tool_call,
        tool_result, and response entries. ``final_content`` is the text of the
        final message part, or None.
    """
    steps: list[dict[str, Any]] = []
    final_content: str | None = None

    for msg in all_messages:
        parts = getattr(msg, "parts", [])
        if not parts:
            continue
        for part in parts:
            kind_str = _get_part_kind(part)
            if kind_str in ("tool-call", "tool_call"):
                steps.append({
                    "type": "tool_call",
                    "tool": getattr(part, "tool_name", ""),
                    "args": getattr(part, "args", {}),
                    "duration_ms": None,
                })
            elif kind_str in ("tool-return", "tool_return"):
                steps.append({
                    "type": "tool_result",
                    "tool": getattr(part, "tool_name", ""),
                    "result": getattr(part, "content", None),
                    "duration_ms": None,
                })
            elif kind_str in ("final", "return", "text"):
                final_content = getattr(part, "content", "") or ""

    if final_content:
        steps.append({
            "type": "response",
            "content": final_content,
            "duration_ms": None,
        })

    return steps, final_content


def _ensure_tool_call_steps(steps: list[dict[str, Any]]) -> None:
    """Create synthetic tool_call steps for tool_result steps that lack a matching tool_call.

    Some model paths (e.g. gpt-4o-mini via certain providers) may produce
    ToolReturnPart messages without a corresponding ToolCallPart in
    ``all_messages()``. This ensures the scoring engine still sees the tool
    as having been called.
    """
    called = {s["tool"] for s in steps if s.get("type") == "tool_call"}
    for i, step in enumerate(steps):
        if step.get("type") != "tool_result":
            continue
        tool = step.get("tool", "")
        if tool and tool not in called:
            steps.insert(i, {
                "type": "tool_call",
                "tool": tool,
                "args": {},
                "duration_ms": None,
            })
            called.add(tool)


def _extract_cost(result: Any) -> dict[str, Any] | None:
    """Extract usage/cost if the result has a .usage() method (pydantic-ai >=0.0.10).

    Args:
        result: The PydanticAI ``AgentRunResult`` object.

    Returns:
        A dict with ``input_tokens``, ``output_tokens``, ``total_tokens``, and
        ``cost_usd`` (currently 0), or None if usage information is unavailable.
    """
    if not hasattr(result, "usage"):
        return None
    try:
        usage = result.usage()
    except Exception:
        return None
    if usage is None:
        return None
    return {
        "input_tokens": getattr(usage, "request_tokens", 0),
        "output_tokens": getattr(usage, "response_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "cost_usd": 0.0,
    }
