"""Repeated-attempt escalation into the shared quarantine machinery (M39-04).

Injection attempts are counted per workload over a rolling window. Crossing the
threshold escalates to auto-quarantine through the same ``QuarantineService``
drift uses, so a workload under sustained injection attack leaves admission.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from hiveplane.defense.events import SecurityEvent, SecurityEventKind, SecurityEventStore
from hiveplane.tenancy import DEFAULT_CONTEXT


class QuarantineAction(Protocol):
    """Structural type for the quarantine service M39 escalates into."""

    def quarantine(
        self,
        workload: str,
        *,
        reason: str,
        severity: object | None = None,
        actor: str = "defense",
        ctx: object = DEFAULT_CONTEXT,
    ) -> object: ...


class AttemptEscalator:
    """Records injection attempts and escalates repeat offenders."""

    def __init__(
        self,
        events: SecurityEventStore,
        *,
        threshold: int = 3,
        window_seconds: int = 3600,
        quarantine_provider: Callable[[], QuarantineAction | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._events = events
        self._threshold = max(1, threshold)
        self._window = timedelta(seconds=max(0, window_seconds))
        self._quarantine_provider = quarantine_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"sec-{uuid4().hex}")

    def record_injection(
        self,
        *,
        run_id: str | None,
        workload: str | None,
        detector_id: str | None = None,
        detector_version: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> bool:
        """Record an injection event; quarantine if the threshold is crossed.

        Returns ``True`` when the workload was escalated to quarantine.
        """
        now = self._clock()
        self._events.add(
            SecurityEvent(
                event_id=self._id_factory(),
                run_id=run_id,
                workload_id=workload,
                kind=SecurityEventKind.INJECTION,
                detector_id=detector_id,
                detector_version=detector_version,
                detail=detail or {},
                created_at=now,
            )
        )
        if workload is None:
            return False
        recent = self._events.list_events(
            workload=workload,
            kind=SecurityEventKind.INJECTION,
            since=now - self._window,
        )
        if len(recent) < self._threshold:
            return False
        quarantine = self._quarantine_provider() if self._quarantine_provider else None
        if quarantine is None:
            return False
        quarantine.quarantine(
            workload,
            reason=f"{len(recent)} injection attempts within the repeat window",
            actor="defense",
        )
        self._events.add(
            SecurityEvent(
                event_id=self._id_factory(),
                run_id=run_id,
                workload_id=workload,
                kind=SecurityEventKind.REPEATED_ATTEMPT,
                detail={"attempts": len(recent)},
                created_at=now,
            )
        )
        return True
