"""LangGraph adapter: invoke a LangGraph agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built agent/graph), calls it to obtain a LangGraph ``CompiledGraph``,
and invokes it with the scenario input. The agent's message list is then
converted to a standardized run envelope with trajectory steps (tool calls,
tool results, and the final response).

Monkey-patches ``langchain_openai.ChatOpenAI.__init__`` to inject the
configured model and endpoint so user agents don't need manual plumbing for
the EvalForge environment variables (``EVALFORGE_FIELD_ENDPOINT``,
``EVALFORGE_FIELD_MODEL``, ``EVALFORGE_FORCE_MODEL``).

Exports:
    LangGraphAdapter: Adapter for LangGraph-based agents.
"""

from __future__ import annotations

import importlib
from typing import Any

from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class LangGraphAdapter(Adapter):
    """Invoke a LangGraph agent via Python import.

    Imports a user module, calls its ``build_agent`` function (or uses a
    pre-built invokable), and invokes the compiled graph with the scenario
    input. Trajectory steps are extracted from the agent's message list.

    Attributes:
        name: Identifier "langgraph".
    """

    name = "langgraph"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Import and invoke a LangGraph agent, returning a run envelope.

        Steps:
        1. Monkey-patch ChatOpenAI to inject model/endpoint defaults.
        2. Import the user's module and resolve the builder function.
        3. Call the builder (respecting its signature) to get an agent.
        4. Compile the graph if needed, optionally attaching a checkpointer.
        5. Invoke the agent with the scenario input.
        6. Extract trajectory and final response from messages.

        Args:
            payload: The invocation payload dict with ``input`` key.
            config: Adapter configuration; requires ``module``, optionally
                ``function``, ``model``, ``checkpointer``/``lg_checkpointer``,
                ``interrupt_before``, ``invoke_config``, and ``callbacks``.

        Returns:
            A run envelope dict with status, output, trajectory, cost, and
            error fields.

        Raises:
            AdapterError: If module import fails, builder is missing, agent
                has no ``invoke`` method, or result lacks ``messages`` key.
        """
        import os
        os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
        endpoint = os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1")
        os.environ.setdefault("OPENAI_BASE_URL", endpoint)
        model = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")
        try:
            from langchain_openai import ChatOpenAI as _CO
            if not getattr(_CO, "_evalforge_patched", False):
                _orig = _CO.__init__
                force = os.environ.get("EVALFORCE_FORCE_MODEL", "0") in {"1", "true", "yes"}
                def _patched(self: Any, *a: Any, **kw: Any) -> Any:
                    if force:
                        kw["model"] = model
                    elif "model" not in kw and "model_name" not in kw:
                        kw["model"] = model
                    return _orig(self, *a, **kw)
                patch_target: Any = _CO
                patch_target.__init__ = _patched
                patch_target._evalforge_patched = True
        except Exception:  # noqa: S110
            pass

        module_name = config.get("module")
        if not module_name:
            raise AdapterError("langgraph adapter requires `module` in config")

        function_name = config.get("function", "build_agent")
        model_override = config.get("model")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is a langgraph agent, install with: pip install evalforge[langgraph]"
            ) from exc

        try:
            builder = getattr(mod, function_name)
        except AttributeError as exc:
            raise AdapterError(
                f"module '{module_name}' has no function '{function_name}'"
            ) from exc

        try:
            kwargs: dict[str, Any] = {}
            if model_override:
                kwargs["model"] = model_override
            # If builder itself is an invokable (compiled graph or agent), use it
            # directly instead of calling it as a factory.
            if hasattr(builder, "invoke"):
                agent = builder
            else:
                import inspect
                sig = inspect.signature(builder)
                required = [p for p in sig.parameters.values()
                           if p.default is inspect._empty
                           and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                          inspect.Parameter.POSITIONAL_OR_KEYWORD)]
                built = builder(**kwargs) if not required else builder(payload, **kwargs)
                # If the builder returned an uncompiled graph, compile it
                # optionally with a MemorySaver checkpointer for persistence.
                if not hasattr(built, "invoke") and hasattr(built, "compile"):
                    try:
                        checkpointer = None
                        cp_kind = config.get("checkpointer") or config.get("lg_checkpointer")
                        if cp_kind in {"memory", "MemorySaver"}:
                            try:
                                from langgraph.checkpoint.memory import MemorySaver
                                checkpointer = MemorySaver()
                            except Exception:
                                checkpointer = None
                        agent = (
                            built.compile(checkpointer=checkpointer)
                            if checkpointer is not None
                            else built.compile()
                        )
                    except Exception:
                        agent = built
                else:
                    agent = built
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not hasattr(agent, "invoke"):
            raise AdapterError(
                "build_agent must return an object with an .invoke() method"
            )

        user_input = payload.get("input", "")
        import uuid as _uuid
        _tid = str(_uuid.uuid4())
        initial_state = {"messages": [{"role": "user", "content": user_input}], "task_id": _tid}

        try:
            import os
            invoke_config: dict[str, Any] = {"configurable": {"thread_id": _tid, "task_id": _tid}}
            if "callbacks" in config:
                invoke_config["callbacks"] = config["callbacks"]
            if "checkpointer" in config and "lg_checkpointer" not in config:
                config["lg_checkpointer"] = config["checkpointer"]
            if "interrupt_before" in config:
                invoke_config["interrupt_before"] = config["interrupt_before"]
            if "invoke_config" in config and isinstance(config["invoke_config"], dict):
                ic = dict(invoke_config)
                ic.update(config["invoke_config"])
                invoke_config = ic
            result = agent.invoke(initial_state, invoke_config)
        except Exception as exc:
            raise AdapterError(f"agent invocation failed: {exc}") from exc

        if not isinstance(result, dict) or "messages" not in result:
            raise AdapterError("agent result missing 'messages' key")

        messages = result["messages"]
        steps, final_content = _extract_trajectory(messages)

        return {
            "schema_version": "evalforge.run_envelope.v1",
            "status": "completed",
            "output": {"final": final_content, "structured": None},
            "trajectory": {"steps": steps},
            "cost": None,
            "error": None,
        }


def _extract_trajectory(
    messages: list[Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Extract trajectory steps and final content from a LangGraph message list.

    Iterates through messages to identify tool calls, tool results, and the
    final AI response. Tool calls produce a ``tool_call`` step, tool responses
    produce a ``tool_result`` step, and the last non-tool AI message produces a
    ``response`` step.

    Args:
        messages: A list of LangChain/LangGraph message objects.

    Returns:
        A tuple of (steps_list, final_content_string). ``steps_list`` contains
        dicts with type, tool/args (for tool_call), tool/result (for
        tool_result), or content (for response). ``final_content`` is the text
        of the last AI response without tool calls, or None.
    """
    steps: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                steps.append({
                    "type": "tool_call",
                    "tool": tc.get("name", ""),
                    "args": tc.get("args", {}),
                    "duration_ms": None,
                })
        elif getattr(msg, "type", None) == "tool":
            steps.append({
                "type": "tool_result",
                "tool": getattr(msg, "name", msg.tool_call_id) or "",
                "result": getattr(msg, "content", ""),
                "duration_ms": None,
            })

    final_content: str | None = None
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai" and not getattr(msg, "tool_calls", None):
            final_content = getattr(msg, "content", None) or ""
            break

    if final_content is not None:
        steps.append({
            "type": "response",
            "content": final_content,
            "duration_ms": None,
        })

    return steps, final_content
