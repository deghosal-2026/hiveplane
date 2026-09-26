"""Taint marks and provenance propagation through a run (M39-02).

Untrusted content (third-party tool output, fetched web content, trigger
payloads) is marked as it enters the run. Marks are the union-propagated lineage
of a value; a destructive tool call is denied by default while any untrusted
mark is live, unless the tool is explicitly allow-listed for untrusted input.
"""

from __future__ import annotations

import threading
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TrustTag(StrEnum):
    """Provenance tag attached to a value in the run."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    DERIVED = "derived"


class TaintSource(BaseModel):
    """Where a mark originated (a tool, trigger, fetched page, or operator)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    source_id: str


class TaintMark(BaseModel):
    """A provenance record for a value that entered the run."""

    model_config = ConfigDict(extra="forbid")

    mark_id: str
    tag: TrustTag
    source: TaintSource
    derived_from: list[str] = Field(default_factory=list)


class TaintDecision(BaseModel):
    """The result of gating a destructive call against live taint marks."""

    model_config = ConfigDict(extra="forbid")

    blocked: bool
    reason: str
    sources: list[TaintSource] = Field(default_factory=list)


class TaintTracker:
    """Collects taint marks for a single run."""

    def __init__(self) -> None:
        self._marks: list[TaintMark] = []
        self._counter = 0

    def mark(
        self,
        *,
        kind: str,
        source_id: str,
        tag: TrustTag = TrustTag.UNTRUSTED,
    ) -> TaintMark:
        """Record a new mark for a value entering the run."""
        record = TaintMark(
            mark_id=self._next_id(),
            tag=tag,
            source=TaintSource(kind=kind, source_id=source_id),
        )
        self._marks.append(record)
        return record

    def mark_derived(
        self,
        *,
        kind: str,
        source_id: str,
        derived_from: list[str],
    ) -> TaintMark:
        """Record a computed value; it inherits the union of its parents' trust."""
        parents = [mark for mark in self._marks if mark.mark_id in derived_from]
        parent_tag = (
            TrustTag.UNTRUSTED
            if any(mark.tag is not TrustTag.TRUSTED for mark in parents)
            else TrustTag.TRUSTED
        )
        tag = TrustTag.DERIVED if parent_tag is TrustTag.UNTRUSTED else TrustTag.TRUSTED
        record = TaintMark(
            mark_id=self._next_id(),
            tag=tag,
            source=TaintSource(kind=kind, source_id=source_id),
            derived_from=list(derived_from),
        )
        self._marks.append(record)
        return record

    def marks(self) -> list[TaintMark]:
        """Return every mark recorded for the run."""
        return list(self._marks)

    def untrusted_sources(self) -> list[TaintSource]:
        """Return the sources of all live untrusted (or derived-untrusted) marks."""
        return [
            mark.source
            for mark in self._marks
            if mark.tag in {TrustTag.UNTRUSTED, TrustTag.DERIVED}
        ]

    def has_untrusted(self) -> bool:
        """Whether any untrusted value is live in the run."""
        return bool(self.untrusted_sources())

    def gate_destructive(
        self,
        *,
        tool_id: str,
        allow_untrusted: bool = False,
    ) -> TaintDecision:
        """Decide whether a destructive tool may run given live taint."""
        if allow_untrusted:
            return TaintDecision(blocked=False, reason="tool allow-listed for untrusted input")
        sources = self.untrusted_sources()
        if not sources:
            return TaintDecision(blocked=False, reason="no untrusted input")
        names = ", ".join(source.source_id for source in sources)
        return TaintDecision(
            blocked=True,
            reason=f"untrusted input from {names} cannot reach destructive tool {tool_id}",
            sources=sources,
        )

    def _next_id(self) -> str:
        self._counter += 1
        return f"taint-{self._counter}"


class TaintRegistry:
    """Run-scoped taint trackers, keyed by run id."""

    def __init__(self) -> None:
        self._trackers: dict[str, TaintTracker] = {}
        self._lock = threading.RLock()

    def for_run(self, run_id: str) -> TaintTracker:
        """Return (creating if needed) the tracker for a run."""
        with self._lock:
            tracker = self._trackers.get(run_id)
            if tracker is None:
                tracker = TaintTracker()
                self._trackers[run_id] = tracker
            return tracker

    def clear(self, run_id: str) -> None:
        """Forget a run's taint (called when the run ends)."""
        with self._lock:
            self._trackers.pop(run_id, None)
