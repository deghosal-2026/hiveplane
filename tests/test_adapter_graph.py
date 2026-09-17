"""Tests for the compiled-graph protocol."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hiveplane.adapters.graph import CompiledGraph, GraphSnapshot


class _Snapshot:
    @property
    def next(self) -> tuple[str, ...]:
        return ("gate",)

    @property
    def values(self) -> dict[str, Any]:
        return {"note": "a"}


class _Graph:
    def stream(
        self, payload: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        yield {"note": "a"}

    def get_state(self, config: dict[str, Any]) -> GraphSnapshot:
        return _Snapshot()


def test_protocols_are_runtime_checkable() -> None:
    assert isinstance(_Graph(), CompiledGraph)
    assert isinstance(_Snapshot(), GraphSnapshot)
    assert not isinstance(object(), CompiledGraph)
