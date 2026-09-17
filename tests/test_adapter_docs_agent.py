"""Tests for the bundled LangGraph example workload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from hiveplane.adapters.loader import EntrypointLoader

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Ctx:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.model_identity = "openai/gpt-4o/2024-08-06"
        self.tool_calls: list[Any] = []
        self.usage: list[dict[str, Any]] = []
        self.checkpoints = 0

    def tool_call(self, tool_id: str, **kwargs: object) -> Any:
        result = type("R", (), {"tool_id": tool_id, "outcome": "allowed"})()
        self.tool_calls.append(result)
        return result

    def report_usage(self, **kwargs: object) -> None:
        self.usage.append(dict(kwargs))

    def checkpoint(self) -> None:
        self.checkpoints += 1


def test_docs_agent_graph_loads() -> None:
    graph: Any = EntrypointLoader(root=_PROJECT_ROOT).load_object("examples.docs_agent:graph")
    assert callable(getattr(graph, "stream", None))
    assert callable(getattr(graph, "get_state", None))


def test_docs_agent_calls_tool_and_interrupts() -> None:
    graph: Any = EntrypointLoader(root=_PROJECT_ROOT).load_object("examples.docs_agent:graph")
    ctx = _Ctx()
    config = {"configurable": {"thread_id": "run-1", "hiveplane_ctx": ctx}}

    chunks = list(graph.stream({"task": {"issue": "README"}}, config, stream_mode="values"))

    assert any("__interrupt__" in chunk for chunk in chunks)
    assert len(ctx.tool_calls) == 1
    assert len(ctx.usage) == 1
    assert ctx.checkpoints >= 1
    assert graph.get_state(config).next
