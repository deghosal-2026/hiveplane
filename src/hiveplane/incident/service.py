"""Incident-mode service: pause/resume the fleet with attribution (M53, D36)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.config import Settings
from hiveplane.incident.models import HaltScope, IncidentRecord
from hiveplane.incident.store import IncidentStore, build_incident_store
from hiveplane.persistence.audit import AuditLog


class NoActiveIncidentError(RuntimeError):
    """Raised when resuming with no active incident."""


def _new_incident_id() -> str:
    return f"inc-{uuid4().hex[:16]}"


def _covers(
    record: IncidentRecord, *, tenant_id: str | None, workload: str | None
) -> bool:
    """Return whether an active incident halts the given tenant/workload."""
    if record.scope is HaltScope.FLEET:
        return True
    if record.scope is HaltScope.TENANT:
        return tenant_id is not None and record.scope_ref == tenant_id
    return workload is not None and record.scope_ref == workload


class IncidentService:
    """Sets and lifts the global halt flag, draining and broadcasting on pause."""

    def __init__(
        self,
        store: IncidentStore,
        *,
        audit: AuditLog | None = None,
        notifier: Callable[[IncidentRecord], list[str]] | None = None,
        drain: Callable[[], list[str]] | None = None,
        undrain: Callable[[], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        incident_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._notifier = notifier
        self._drain = drain
        self._undrain = undrain
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = incident_id_factory or _new_incident_id

    @classmethod
    def build(cls, settings: Settings | None = None) -> IncidentService:
        """Build an incident service from the configured store."""
        return cls(build_incident_store(settings))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def pause(
        self,
        *,
        actor: str,
        reason: str | None = None,
        scope: HaltScope = HaltScope.FLEET,
        scope_ref: str | None = None,
        trigger: str = "operator",
    ) -> IncidentRecord:
        """Halt the fleet (or a scope): record, drain triggers, and broadcast."""
        record = IncidentRecord(
            incident_id=self._id_factory(),
            scope=scope,
            scope_ref=scope_ref,
            trigger=trigger,
            reason=reason,
            actor=actor,
            halted_at=self._clock(),
        )
        self._store.save(record)
        if self._drain is not None:
            with contextlib.suppress(Exception):
                self._drain()
        if self._notifier is not None:
            try:
                owners = self._notifier(record)
            except Exception:
                owners = []
            if owners:
                record = record.model_copy(update={"owners_notified": owners})
                self._store.save(record)
        if self._audit is not None:
            self._audit.append(
                actor,
                "fleet.paused",
                record.incident_id,
                detail=reason or f"scope={record.scope.value}",
            )
        return record

    def resume(self, *, actor: str, incident_id: str | None = None) -> IncidentRecord:
        """Lift the halt, recording who resumed it and when."""
        target = (
            self._store.get(incident_id)
            if incident_id is not None
            else self._store.active()
        )
        if target is None or target.resumed_at is not None:
            raise NoActiveIncidentError("no active incident to resume")
        resumed = target.model_copy(
            update={"resumed_at": self._clock(), "resumed_by": actor}
        )
        self._store.save(resumed)
        if self._undrain is not None:
            with contextlib.suppress(Exception):
                self._undrain()
        if self._audit is not None:
            self._audit.append(
                actor,
                "fleet.resumed",
                resumed.incident_id,
                detail=f"halted_by={resumed.actor}",
            )
        return resumed

    def active(self) -> IncidentRecord | None:
        """Return the most recent active incident, if any."""
        return self._store.active()

    def active_for(self, tenant_id: str) -> IncidentRecord | None:
        """Return the active incident visible to a tenant, if any.

        Applies the same visibility rule as :meth:`history_for`: fleet-wide and
        the tenant's own scope are visible; another tenant's scoped incident and
        workload-scoped incidents are withheld.
        """
        active = self.active()
        if active is None:
            return None
        if active.scope is HaltScope.FLEET:
            return active
        if active.scope is HaltScope.TENANT and active.scope_ref == tenant_id:
            return active
        return None

    def history(self) -> list[IncidentRecord]:
        """Return all incidents, newest first."""
        return list(reversed(self._store.list()))

    def history_for(self, tenant_id: str) -> list[IncidentRecord]:
        """Return incidents visible to a tenant: fleet-wide and its own scope.

        Workload-scoped incidents are not tenant-tagged, so they are withheld
        from the general history view rather than leaked across tenants.
        """
        return [
            record
            for record in self.history()
            if record.scope is HaltScope.FLEET
            or (record.scope is HaltScope.TENANT and record.scope_ref == tenant_id)
        ]

    def halted(self, *, tenant_id: str | None = None, workload: str | None = None) -> bool:
        """Return whether the given scope is halted; fail closed on read error."""
        try:
            active = self._store.active_all()
        except Exception:
            return True
        return any(
            _covers(record, tenant_id=tenant_id, workload=workload)
            for record in active
        )


class IncidentHaltGate:
    """Admission gate adapter that consults the incident service."""

    def __init__(self, service: IncidentService) -> None:
        self._service = service

    def halted(self, *, tenant_id: str | None = None, workload: str | None = None) -> bool:
        """Return whether admission must refuse for the given scope."""
        return self._service.halted(tenant_id=tenant_id, workload=workload)
