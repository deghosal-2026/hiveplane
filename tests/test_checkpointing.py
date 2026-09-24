"""Tests for durable LangGraph checkpointing (M23, #122).

A paused graph must resume from its last node after the process that owned it
goes away. These exercise real LangGraph graphs against the JSON-file saver,
including a fresh saver instance that stands in for a control-plane restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from hiveplane.checkpointing import JsonFileCheckpointSaver, default_checkpointer
from hiveplane.config import get_settings


class _State(TypedDict, total=False):
    steps: list[str]
    result: str


def _first(state: _State) -> _State:
    return {"steps": [*state.get("steps", []), "first"]}


def _gate(state: _State) -> _State:
    interrupt("approve?")
    return {"steps": [*state.get("steps", []), "gate"]}


def _last(state: _State) -> _State:
    return {"steps": [*state.get("steps", []), "last"], "result": "done"}


def _build(saver: object) -> Any:
    builder = StateGraph(_State)
    builder.add_node("first", _first)
    builder.add_node("gate", _gate)
    builder.add_node("last", _last)
    builder.add_edge(START, "first")
    builder.add_edge("first", "gate")
    builder.add_edge("gate", "last")
    builder.add_edge("last", END)
    return builder.compile(checkpointer=saver)  # type: ignore[arg-type]


def test_checkpoint_state_survives_a_new_saver_instance(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    config = {"configurable": {"thread_id": "run-1"}}

    graph = _build(JsonFileCheckpointSaver(path))
    paused = graph.invoke({}, config)

    assert paused["steps"] == ["first"]

    # A new saver over the same file stands in for a restarted control plane.
    resumed_graph = _build(JsonFileCheckpointSaver(path))
    resumed = resumed_graph.invoke(Command(resume=True), config)

    assert resumed["result"] == "done"
    assert resumed["steps"] == ["first", "gate", "last"]


def test_checkpoint_file_is_written(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    saver = JsonFileCheckpointSaver(path)
    graph = _build(saver)

    graph.invoke({}, {"configurable": {"thread_id": "run-1"}})

    assert path.is_file()


def test_default_checkpointer_is_in_memory_without_a_configured_path() -> None:
    assert isinstance(default_checkpointer(), InMemorySaver)


def test_default_checkpointer_uses_the_configured_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoints" / "graph.json"
    monkeypatch.setenv("HIVEPLANE_EXECUTION__CHECKPOINT_PATH", str(path))
    get_settings.cache_clear()
    try:
        saver = default_checkpointer()
    finally:
        get_settings.cache_clear()

    assert isinstance(saver, JsonFileCheckpointSaver)
    assert saver.path == path
