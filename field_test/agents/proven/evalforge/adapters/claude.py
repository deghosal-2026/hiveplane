"""Claude Agent SDK adapter: invoke a Claude agent via Python import.

Imports a user-supplied Python module that exposes a ``build_agent`` function
(or a pre-built client instance), calls it to obtain a ``ClaudeSDKClient``-like
object, and invokes it with ``client.query(prompt)`` + ``client.receive_response()``
(async). The returned messages are converted to a standardized run envelope
with trajectory steps (tool calls, tool results, and the final response).

The adapter duck-types against both the real ``claude_agent_sdk`` client and
mock fixtures. Content blocks (``tool_use`` → ``tool_call``, ``tool_result`` →
``tool_result``, ``text`` → ``response``) drive trajectory extraction. Cost is
extracted from the ``ResultMessage.model_usage`` when available.

Exports:
    ClaudeAgentSDKAdapter: Adapter for Claude Agent SDK-based agents.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

from evalforge.adapters._model_patch import apply_model_patch
from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError


class ClaudeAgentSDKAdapter(Adapter):
    """Invoke a Claude Agent SDK client via Python import.

    Attributes:
        name: Identifier "claude".
    """

    name = "claude"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        apply_model_patch()
        module_name = config.get("module")
        if not module_name:
            raise AdapterError("claude adapter requires `module` in config")

        function_name = config.get("function", "build_agent")

        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            raise AdapterError(
                f"cannot import module '{module_name}': {exc}. "
                "If this is a claude agent, install with: "
                "pip install evalforge[claude]"
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
                client = callable_builder(payload) if required else callable_builder()
            else:
                client = builder
        except Exception as exc:
            raise AdapterError(f"build_agent failed: {exc}") from exc

        if not (hasattr(client, "query") or hasattr(client, "run")):
            raise AdapterError("build_agent must return an object with .query() or .run()")

        user_input = payload.get("input", "")

        try:
            messages = asyncio.run(_run_client(client, user_input))
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"claude agent failed: {exc}") from exc

        steps = _extract_trajectory(messages)
        final_content, structured = _extract_output(messages, steps)
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
            "cost": _extract_cost(messages),
            "error": None,
        }


async def _run_client(client: Any, user_input: str) -> list[Any]:
    """Drive the Claude client: ``query`` then collect ``receive_response``."""
    if hasattr(client, "query") and hasattr(client, "receive_response"):
        await client.query(user_input)
        return [msg async for msg in client.receive_response()]
    if hasattr(client, "run"):
        result = client.run(user_input)
        if inspect.iscoroutine(result):
            result = await result
        return result if isinstance(result, list) else [result]
    raise AdapterError("claude client must expose query+receive_response or run")


def _extract_trajectory(messages: list[Any]) -> list[dict[str, Any]]:
    """Extract trajectory steps from Claude message blocks.

    Walks each message's ``content`` blocks: ``tool_use`` → ``tool_call``,
    ``tool_result`` → ``tool_result`` (name resolved via the last ``tool_use``
    id), ``text`` on the final assistant message → ``response``.
    """
    steps: list[dict[str, Any]] = []
    id_to_name: dict[str, str] = {}
    for message in messages:
        blocks = getattr(message, "content", None)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            block_type = getattr(block, "type", "") or _block_type(block)
            if block_type == "tool_use":
                name = getattr(block, "name", "") or ""
                block_id = getattr(block, "id", "")
                if block_id:
                    id_to_name[block_id] = name
                steps.append({
                    "type": "tool_call",
                    "tool": name,
                    "args": getattr(block, "input", {}) or {},
                    "duration_ms": None,
                })
            elif block_type == "tool_result":
                tool_id = getattr(block, "tool_use_id", "")
                name = id_to_name.get(tool_id, "")
                steps.append({
                    "type": "tool_result",
                    "tool": name,
                    "result": getattr(block, "content", None),
                    "duration_ms": None,
                })
    return steps


def _extract_output(messages: list[Any], steps: list[dict[str, Any]]) -> tuple[str | None, Any]:
    """Extract the final output and structured data from Claude messages.

    Prefers a ``ResultMessage``: its ``structured_output`` when present, else
    ``result`` text. Falls back to the last assistant ``text`` block.
    """
    for message in reversed(messages):
        subtype = getattr(message, "subtype", None) or getattr(message, "type", "")
        if subtype == "result" or subtype == "ResultMessage":
            structured = getattr(message, "structured_output", None)
            result_text = getattr(message, "result", None)
            if structured is not None and isinstance(structured, dict):
                return result_text or str(structured), structured
            if isinstance(result_text, str) and result_text:
                parsed = _parse_jsonish(result_text)
                if isinstance(parsed, dict):
                    return result_text, parsed
                return result_text, None
            break
    last_text: str | None = None
    for message in reversed(messages):
        blocks = getattr(message, "content", None)
        if not isinstance(blocks, list):
            continue
        for block in reversed(blocks):
            block_type = getattr(block, "type", "") or _block_type(block)
            if block_type == "text":
                text = getattr(block, "text", None)
                if isinstance(text, str) and text:
                    last_text = text
                    break
        if last_text:
            break
    if last_text:
        parsed = _parse_jsonish(last_text)
        if isinstance(parsed, dict):
            return last_text, parsed
    return last_text, None


def _extract_cost(messages: list[Any]) -> dict[str, Any] | None:
    """Extract usage/cost from a ``ResultMessage.model_usage`` dict.

    ``model_usage`` maps model name → ``ModelUsage`` with ``inputTokens``,
    ``outputTokens``, and ``costUSD``. Values are summed across models.
    """
    for message in reversed(messages):
        model_usage = getattr(message, "model_usage", None)
        if not isinstance(model_usage, dict) or not model_usage:
            continue
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        for usage in model_usage.values():
            input_tokens += getattr(usage, "inputTokens", 0) or 0
            output_tokens += getattr(usage, "outputTokens", 0) or 0
            cost_usd += getattr(usage, "costUSD", 0.0) or 0.0
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": cost_usd,
        }
    return None


def _block_type(block: Any) -> str:
    """Infer a content block's type from its attributes."""
    if hasattr(block, "text"):
        return "text"
    if hasattr(block, "name") and hasattr(block, "input"):
        return "tool_use"
    if hasattr(block, "tool_use_id"):
        return "tool_result"
    if hasattr(block, "thinking"):
        return "thinking"
    return ""


def _parse_jsonish(value: str) -> Any:
    """Parse a JSON string, returning the original value on failure."""
    import json
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
