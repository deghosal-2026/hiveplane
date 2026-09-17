"""The reporting seam adapters use to tell the control plane what happened.

Adapters report; they do not decide. ``RunService`` satisfies this protocol
structurally, so wiring passes the service itself as the reporter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from hiveplane.core.event import EventType
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport


@runtime_checkable
class RunReporter(Protocol):
    """The control-plane operations a runtime adapter may report through."""

    def get(self, run_id: str) -> Run:
        """Return the current run aggregate."""
        ...

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: JsonValue | None = None,
    ) -> Run:
        """Move a run to a target state, recording the transition."""
        ...

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        """Record priced usage for a run and enforce budget."""
        ...

    def record_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Append an attributed event to a run's history."""
        ...
