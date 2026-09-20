"""CrewAI adapter: invoke a CrewAI crew via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built Crew instance), calls it to obtain a ``crewai.Crew``, and
invokes it with ``crew.kickoff(inputs=...)``. The returned ``CrewOutput`` is
converted to a standardized run envelope with trajectory steps (tool calls,
tool results, and the final response).

Cost is extracted from ``CrewOutput.token_usage`` when available (input,
completion/output, and total tokens).

Exports:
    CrewAIAdapter: Adapter for CrewAI-based agents.
"""

from __future__ import annotations

import importlib
from typing import Any

from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class CrewAIAdapter(Adapter):
    """Invoke a CrewAI crew via Python import.

    Imports a user module, calls its ``build_agent`` function (or uses a
    pre-built Crew), and invokes it with ``crew.kickoff(inputs=...)``.
    Trajectory steps are extracted from the crew's task outputs.

    Attributes:
        name: Identifier "crewai".
    """

    name = "crewai"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Import and invoke a CrewAI crew, returning a run envelope.

        Steps:
        1. Import the user's module and resolve the builder function.
        2. If the builder is a callable factory, call it (respecting its
           signature); otherwise treat it as a pre-built Crew instance.
        3. Verify the crew has a ``kickoff`` method.
        4. Invoke the crew with the scenario input as ``inputs``.
        5. Extract trajectory and final content from task outputs.
        6. Extract usage/cost information if available.

        Args:
            payload: The invocation payload dict with ``input`` key.
            config: Adapter configuration; requires ``module``, optionally
                ``function``, ``model``, and ``inputs``.

        Returns:
            A run envelope dict with status, output (final + structured),
            trajectory, cost, and error fields.

        Raises:
            AdapterError: If module import fails, builder is missing, crew
                has no ``kickoff`` method, or invocation fails.
        """
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("crewai adapter requires `module` in config")

        function_name = config.get("function", "build_agent")
        model_override = config.get("model")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is a crewai agent, install with: pip install evalforge[crewai]"
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
            # Treat non-function/class attributes as prebuilt crew instances
            # rather than calling them as factories.
            if not (
                _inspect.isfunction(builder)
                or _inspect.ismethod(builder)
                or _inspect.isclass(builder)
            ):
                crew = builder
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
                crew = (
                    callable_builder(**kwargs) if not required
                    else callable_builder(payload, **kwargs)
                )
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not hasattr(crew, "kickoff"):
            raise AdapterError(
                "build_agent must return an object with a .kickoff() method"
            )

        user_input = payload.get("input", "")
        inputs: dict[str, Any] = {"input": user_input}
        if isinstance(config.get("inputs"), dict):
            for key, value in config["inputs"].items():
                inputs[key] = value

        try:
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            raise AdapterError(f"crew kickoff failed: {exc}") from exc

        steps, final_content = _extract_trajectory(result)

        structured = getattr(result, "json_dict", None)
        if structured is not None and not isinstance(structured, dict):
            structured = None

        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": structured},
            "trajectory": {"steps": steps},
            "cost": _extract_cost(result),
            "error": None,
        }


def _extract_trajectory(
    result: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    """Extract trajectory steps and final content from a CrewAI CrewOutput.

    Walks ``tasks_output[].messages`` to identify:
    - Tool calls (assistant messages with ``tool_calls``)
    - Tool results (tool messages with ``tool_call_id``)
    - Final response (last assistant message content)

    Args:
        result: The CrewAI ``CrewOutput`` object.

    Returns:
        A tuple of (steps_list, final_content_string). ``steps_list`` contains
        tool_call, tool_result, and response entries. ``final_content`` is the
        crew's final raw output, or None.
    """
    steps: list[dict[str, Any]] = []
    tasks = getattr(result, "tasks_output", None) or []
    for task in tasks:
        messages = getattr(task, "messages", None) or []
        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = (
                        tc.get("function", {})
                        if isinstance(tc, dict)
                        else getattr(tc, "function", {})
                    )
                    name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
                    args_raw = (
                        fn.get("arguments", "{}")
                        if isinstance(fn, dict)
                        else getattr(fn, "arguments", "{}")
                    )
                    args = _parse_jsonish(args_raw) if isinstance(args_raw, str) else args_raw
                    steps.append({
                        "type": "tool_call",
                        "tool": name,
                        "args": args if isinstance(args, dict) else {},
                        "duration_ms": None,
                    })
            elif role == "tool":
                name = msg.get("name", "") if isinstance(msg, dict) else getattr(msg, "name", "")
                steps.append({
                    "type": "tool_result",
                    "tool": name,
                    "result": (
                        msg.get("content", "")
                        if isinstance(msg, dict)
                        else getattr(msg, "content", "")
                    ),
                    "duration_ms": None,
                })

    final_content = getattr(result, "raw", None) or ""
    if final_content:
        steps.append({
            "type": "response",
            "content": final_content,
            "duration_ms": None,
        })

    return steps, final_content or None


def _extract_cost(result: Any) -> dict[str, Any] | None:
    """Extract usage/cost if the crew exposed ``token_usage``.

    Args:
        result: The CrewAI ``CrewOutput`` object.

    Returns:
        A dict with ``input_tokens``, ``output_tokens``, ``total_tokens``, and
        ``cost_usd`` (currently 0), or None if usage information is unavailable.
    """
    usage = getattr(result, "token_usage", None)
    if usage is None:
        return None
    return {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cost_usd": 0.0,
    }


def _parse_jsonish(value: str) -> Any:
    """Parse a JSON string, returning the original value on failure."""
    import json
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
