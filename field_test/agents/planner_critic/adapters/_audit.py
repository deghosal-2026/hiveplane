"""Shared audit trail (F-46 / T8) — lightweight event log for adapters.

Every adapter can record milestones (plan requested, approved, re-gate check,
replan triggered) into a shared :class:`AuditTrail` so that callers can
observe and assert on the lifecycle without coupling to adapter internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditEvent:
    """A single audit event recorded by an adapter.

    Attributes:
        adapter: Short name of the adapter that recorded the event
            (e.g. ``"raw"``, ``"langgraph"``, ``"crewai"``).
        event: The event type — one of ``"plan_requested"``,
            ``"plan_approved"``, ``"re_gate_check"``, ``"replan"``.
        plan_id: The plan id the event relates to, or ``None``.
        details: Arbitrary key-value extras (findings count, task id, etc.).
    """

    adapter: str
    event: str
    plan_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)


class AuditTrail:
    """Ordered sequence of :class:`AuditEvent` records.

    Usage::

        trail = AuditTrail()
        trail.record(AuditEvent("raw", "plan_requested"))
        assert trail.last_event().event == "plan_requested"
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def get_events(self) -> list[AuditEvent]:
        return list(self.events)

    def last_event(self) -> AuditEvent | None:
        return self.events[-1] if self.events else None
