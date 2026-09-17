"""The minimal compiled-graph surface the LangGraph adapter depends on (M17).

Keeping this as a Protocol lets the adapter stay typed without importing
langgraph at module import time; the concrete object is duck-typed at runtime.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphSnapshot(Protocol):
    """A point-in-time view of a compiled graph's state."""

    @property
    def next(self) -> tuple[str, ...]:
        """Nodes scheduled to run next; empty when the graph is finished."""
        ...

    @property
    def values(self) -> dict[str, Any]:
        """The current graph state values."""
        ...


@runtime_checkable
class CompiledGraph(Protocol):
    """The subset of a compiled LangGraph the adapter drives."""

    def stream(
        self, payload: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        """Stream state values as the graph advances."""
        ...

    def get_state(self, config: dict[str, Any]) -> GraphSnapshot:
        """Return the current state snapshot for a thread."""
        ...
