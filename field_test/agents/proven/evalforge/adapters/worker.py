"""Worker process for IsolatedAdapter. Run via: python -m evalforge.adapters.worker

Reads a JSON payload from stdin containing adapter metadata and the invocation
payload, instantiates the appropriate adapter, and writes a JSON run envelope
to stdout.

The payload structure expected on stdin:
{
    "adapter_type": "langgraph" | "pydantic-ai" | "crewai" | "openai-agents" |
                   "smolagents" | "autogen" | "llamaindex" | "claude" | "adk",
    "module": "<python module path>",
    "function": "<builder function name>",
    "model": "<optional model override>",
    "payload": { <invocation payload> }
}

Exports:
    main: Entry point that reads stdin, dispatches to the appropriate adapter,
        and prints the run envelope.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Sentinel used when a framework's adapter class cannot be imported.
_SENTINEL = object()


def _load_payload() -> dict[str, Any]:
    """Read and parse a JSON object from stdin.

    Returns:
        The parsed JSON payload as a dict.

    Raises:
        RuntimeError: If stdin does not contain a JSON object.
    """
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise RuntimeError("stdin payload must be a JSON object")
    return data


def _try_import_adapter(module_path: str, class_name: str) -> Any:
    """Import an adapter class, returning a sentinel on failure.

    Args:
        module_path: Dotted module path.
        class_name: Class name within the module.

    Returns:
        The adapter class, or ``_SENTINEL`` if the import fails.
    """
    try:
        from importlib import import_module
        mod = import_module(module_path)
        return getattr(mod, class_name)
    except Exception:
        return _SENTINEL


_ADAPTER_TABLE: dict[str, tuple[str, str]] = {
    "langgraph": ("evalforge.adapters.langgraph", "LangGraphAdapter"),
    "pydantic-ai": ("evalforge.adapters.pydantic_ai", "PydanticAIAdapter"),
    "crewai": ("evalforge.adapters.crewai", "CrewAIAdapter"),
    "openai-agents": ("evalforge.adapters.openai_agents", "OpenAIAgentsAdapter"),
    "smolagents": ("evalforge.adapters.smolagents", "SmolagentsAdapter"),
    "autogen": ("evalforge.adapters.autogen", "AutoGenAdapter"),
    "llamaindex": ("evalforge.adapters.llamaindex", "LlamaIndexAdapter"),
    "claude": ("evalforge.adapters.claude", "ClaudeAgentSDKAdapter"),
    "adk": ("evalforge.adapters.adk", "ADKAdapter"),
}

_CACHE: dict[str, Any] = {}


def _adapter_for(kind: str) -> Any | None:
    """Get (possibly cached) adapter instance for the given type string.

    Args:
        kind: Adapter type string (e.g. ``"smolagents"``).

    Returns:
        An adapter instance, or None if the type is unknown or the adapter
        class cannot be imported.
    """
    if kind in _CACHE:
        return _CACHE[kind]
    entry = _ADAPTER_TABLE.get(kind)
    if entry is None:
        _CACHE[kind] = None
        return None
    cls = _try_import_adapter(*entry)
    if cls is _SENTINEL:
        _CACHE[kind] = None
        return None
    inst = cls()
    _CACHE[kind] = inst
    return inst


def _error_envelope(exc: Exception) -> dict[str, Any]:
    """Build a run envelope representing an error state.

    Args:
        exc: The exception that occurred.

    Returns:
        A run envelope with status "error" and the exception's message.
    """
    return {
        "schema_version": "evalforge.run_envelope.v1",
        "status": "error",
        "output": {"final": None, "structured": None},
        "trajectory": {"steps": []},
        "cost": None,
        "error": str(exc),
    }


def main() -> int:
    """Entry point: read payload from stdin, dispatch, and print result.

    Parses the stdin JSON, selects the appropriate adapter instance, calls
    its ``_invoke`` method, and writes the resulting run envelope as JSON
    to stdout. Any exception during processing is caught and rendered as an
    error envelope.

    Returns:
        0 on success (the envelope is always written to stdout).
    """
    data = _load_payload()
    adapter_type = data.get("adapter_type", "")
    invocation_payload = data.get("payload", {})
    config = {
        "module": data.get("module", ""),
        "function": data.get("function", "build_agent"),
        "model": data.get("model"),
    }

    adapter = _adapter_for(adapter_type)
    if adapter is None:
        envelope = _error_envelope(
            RuntimeError(f"unknown or unavailable adapter_type: {adapter_type}")
        )
    else:
        try:
            envelope = adapter._invoke(invocation_payload, config)
        except Exception as exc:
            envelope = _error_envelope(exc)

    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
