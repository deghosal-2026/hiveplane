"""smolagents adapter: invoke a smolagents agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built agent instance), calls it to obtain a ``CodeAgent``-like object,
and invokes it with ``agent.run(task, return_full_result=True)``. The returned
``RunResult`` is converted to a standardized run envelope with trajectory steps
(tool calls, tool results, and the final response).

Cost is extracted from the result's ``token_usage`` when available.

Exports:
    SmolagentsAdapter: Adapter for smolagents-based agents.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from evalforge.adapters._model_patch import apply_model_patch
from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class SmolagentsAdapter(Adapter):
    """Invoke a smolagents agent via Python import.

    Attributes:
        name: Identifier "smolagents".
    """

    name = "smolagents"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        apply_model_patch()
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("smolagents adapter requires `module` in config")

        function_name = config.get("function", "build_agent")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is a smolagents agent, install with: "
                "pip install evalforge[smolagents]"
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
            result = agent.run(task=user_input, return_full_result=True)
        except TypeError:
            result = agent.run(user_input)
        except Exception as exc:
            raise AdapterError(f"agent run failed: {exc}") from exc

        steps = _extract_trajectory(result)
        final_content, structured = _extract_output(result, steps)

        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": structured},
            "trajectory": {"steps": steps},
            "cost": _extract_cost(result),
            "error": None,
        }


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Get attr from a dict or object, with a default."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _extract_trajectory(result: Any) -> list[dict[str, Any]]:
    """Extract trajectory steps from a smolagents ``RunResult``.

    Walks ``result.steps`` (or ``result.memory.get_full_steps()``) to identify
    tool calls, tool results, and the final response. Handles both mock
    ``SimpleNamespace`` objects (``tool_calls`` / ``observations`` /
    ``is_final_answer``) and real smolagents dicts (``model_output_message`` /
    ``action_output``).

    For ``CodeAgent`` steps, the ``tool_calls`` field records
    ``python_interpreter`` as the tool because the agent writes Python code
    that calls user-defined tools internally.  This function parses
    ``code_action`` to extract the actual tool names (e.g. ``get_weather``)
    so scorers can match against ``allowed_tools``.
    """
    steps_raw = getattr(result, "steps", None)
    if steps_raw is None:
        memory = getattr(result, "memory", None)
        if memory is not None and hasattr(memory, "get_full_steps"):
            steps_raw = memory.get_full_steps()
    steps_raw = steps_raw or []

    steps: list[dict[str, Any]] = []
    for step in steps_raw:
        if isinstance(step, dict) and "task" in step and "step_number" not in step:
            continue

        calls = _get(step, "tool_calls", []) or []
        observations = _get(step, "observations", None)
        _is_final = _get(step, "is_final_answer", False)
        action_output = _get(step, "action_output", None)
        code_action = _get(step, "code_action", None)

        if not isinstance(observations, list):
            observations = observations if isinstance(observations, str) else None
        obs_list: list[Any] = observations if isinstance(observations, list) else []

        # For CodeAgent, parse code_action to find real tool calls.
        # When code_action is present, ONLY use parsed calls — the
        # python_interpreter entries in tool_calls are the agent's
        # execution engine, not user-facing tools.
        parsed_calls = _parse_code_action_tools(code_action) if code_action else []
        if code_action:
            obs_str = observations if isinstance(observations, str) else None
            for pname, pargs in parsed_calls:
                steps.append({
                    "type": "tool_call",
                    "tool": pname,
                    "args": pargs,
                    "duration_ms": None,
                })
                if obs_str:
                    steps.append({
                        "type": "tool_result",
                        "tool": pname,
                        "result": obs_str,
                        "duration_ms": None,
                    })
            if action_output:
                steps.append({
                    "type": "response",
"content": (
                        action_output
                        if isinstance(action_output, str)
                        else str(action_output)
                    ),
                    "duration_ms": None,
                })
            continue

        handled_final = False
        for _i, call in enumerate(calls):
            if isinstance(call, dict):
                fn = call.get("function", {}) or {}
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                args_raw = fn.get("arguments", {}) if isinstance(fn, dict) else {}
            else:
                name = _get(call, "name", "") or ""
                args_raw = _get(call, "arguments", None)
            if isinstance(args_raw, str):
                import json
                try:
                    args_raw = json.loads(args_raw)
                except (ValueError, TypeError):
                    pass
            steps.append({
                "type": "tool_call",
                "tool": name,
                "args": args_raw if isinstance(args_raw, dict) else {},
                "duration_ms": None,
            })
            if isinstance(observations, str) and not handled_final:
                steps.append({
                    "type": "tool_result",
                    "tool": name,
                    "result": observations,
                    "duration_ms": None,
                })
                handled_final = True
            elif obs_list:
                obs = obs_list.pop(0)
                if obs_list:
                    obs_list.pop(0)  # skip trailing None
                steps.append({
                    "type": "tool_result",
                    "tool": name,
                    "result": obs,
                    "duration_ms": None,
                })

        if action_output:
            steps.append({
                "type": "response",
                "content": action_output if isinstance(action_output, str) else str(action_output),
                "duration_ms": None,
            })

    return steps


# Builtins and smolagents internals that should not be treated as tool calls
_BUILTIN_NAMES = frozenset({
    "print", "final_answer", "len", "str", "int", "float", "bool", "list",
    "dict", "set", "tuple", "range", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "sum", "min", "max", "abs", "round", "type",
    "isinstance", "issubclass", "getattr", "setattr", "hasattr", "open",
    "import", "None", "True", "False", "self",
})


def _parse_code_action_tools(code_action: Any) -> list[tuple[str, dict[str, Any]]]:
    """Parse Python code to extract tool calls from a CodeAgent step.

    Uses ``ast`` to find function calls, filtering out builtins and
    smolagents internals.  Returns a list of ``(tool_name, args_dict)``.
    """
    if not isinstance(code_action, str) or not code_action.strip():
        return []
    import ast

    try:
        tree = ast.parse(code_action)
    except SyntaxError:
        return []

    calls: list[tuple[str, dict[str, Any]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name in _BUILTIN_NAMES:
            continue
        kwargs: dict[str, Any] = {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            try:
                kwargs[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                kwargs[kw.arg] = None
        calls.append((name, kwargs))
    return calls


def _extract_output(result: Any, steps: list[dict[str, Any]]) -> tuple[str | None, Any]:
    """Extract the final output and structured data from a ``RunResult``."""
    output = _get(result, "output", None)
    if isinstance(output, dict):
        final = str(output)
        return final, output
    if isinstance(output, str) and output:
        return output, None
    # AgentText or similar wrapper
    if output is not None and hasattr(output, "text"):
        return output.text, None
    for step in reversed(steps):
        if step.get("type") == "response" and step.get("content"):
            return step["content"], None
    return str(output) if output is not None else None, None


def _extract_cost(result: Any) -> dict[str, Any] | None:
    """Extract usage/cost from the result's ``token_usage``."""
    usage = _get(result, "token_usage", None)
    if usage is None:
        return None
    input_tokens = _get(usage, "input_tokens", 0) or 0
    output_tokens = _get(usage, "output_tokens", 0) or 0
    total_tokens = _get(usage, "total_tokens", 0) or (input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": 0.0,
    }
