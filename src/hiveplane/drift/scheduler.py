"""Periodic re-certification scheduling and expiry windows (M34-01, M34-07).

Cadence is per workload: the manifest's ``certification.re_cert_interval`` wins,
falling back to the fleet default. ``due`` is deterministic given the clock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.drift.models import (
    CertificationExpiry,
    DriftSchedule,
    DueWorkload,
    ExpiryState,
)
from hiveplane.registry.service import RegistryService

DEFAULT_RE_CERT_INTERVAL = 14 * 86400


class DriftScheduler:
    """Computes re-certification cadence, due workloads, and expiry states."""

    def __init__(
        self,
        registry: RegistryService,
        *,
        default_interval: int = DEFAULT_RE_CERT_INTERVAL,
        renewal_window: int = 3 * 86400,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if default_interval < 1:
            raise ValueError("default_interval must be >= 1 second")
        if renewal_window < 0:
            raise ValueError("renewal_window must be >= 0 seconds")
        self._registry = registry
        self._default_interval = default_interval
        self._renewal_window = renewal_window
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def renewal_window(self) -> int:
        """Return the renewal window in seconds."""
        return self._renewal_window

    def schedule(self, workload: str) -> DriftSchedule:
        """Return the computed schedule for one workload."""
        record = self._registry.get(workload)
        certification = record.manifest.spec.certification
        interval = (
            certification.re_cert_interval
            if certification is not None
            else self._default_interval
        )
        last_certified = certification.certified_at if certification is not None else None
        anchor = last_certified or record.created_at
        next_run = anchor + timedelta(seconds=interval)
        return DriftSchedule(
            schedule_id=f"schedule-{workload}",
            workload=workload,
            interval_seconds=interval,
            last_certified_at=last_certified,
            next_re_cert_run=next_run,
            expires_at=certification.expires_at if certification is not None else None,
            tenant_id=record.tenant_id,
        )

    def schedules(self) -> list[DriftSchedule]:
        """Return schedules for every registered workload, sorted by workload."""
        records = self._registry.list_workloads()
        schedules = [self.schedule(record.name) for record in records]
        schedules.sort(key=lambda schedule: schedule.workload)
        return schedules

    def due(self, *, at: datetime | None = None) -> list[DueWorkload]:
        """Return workloads whose re-certification window is reached or expired."""
        now = at or self._clock()
        due: list[DueWorkload] = []
        for schedule in self.schedules():
            expiry_state = self._expiry_state(schedule.expires_at, now)
            if now >= schedule.next_re_cert_run or expiry_state is ExpiryState.EXPIRED:
                due.append(
                    DueWorkload(
                        workload=schedule.workload,
                        next_re_cert_run=schedule.next_re_cert_run,
                        expires_at=schedule.expires_at,
                        overdue=now >= schedule.next_re_cert_run,
                        expiry_state=expiry_state,
                    )
                )
        due.sort(key=lambda item: (item.next_re_cert_run, item.workload))
        return due

    def expiry(self, workload: str) -> CertificationExpiry:
        """Return the certification expiry/renewal state for one workload."""
        schedule = self.schedule(workload)
        return self._expiry(schedule.workload, schedule.expires_at)

    def expiries(self) -> list[CertificationExpiry]:
        """Return expiry states for every workload, most urgent first."""
        items = [self.expiry(schedule.workload) for schedule in self.schedules()]
        order = {
            ExpiryState.EXPIRED: 0,
            ExpiryState.EXPIRING: 1,
            ExpiryState.VALID: 2,
            ExpiryState.UNKNOWN: 3,
        }
        items.sort(key=lambda item: (order[item.state], item.workload))
        return items

    def _expiry(self, workload: str, expires_at: datetime | None) -> CertificationExpiry:
        state = self._expiry_state(expires_at, self._clock())
        days_remaining = None
        if expires_at is not None:
            days_remaining = (expires_at - self._clock()).total_seconds() / 86400
        return CertificationExpiry(
            workload=workload,
            state=state,
            expires_at=expires_at,
            days_remaining=days_remaining,
        )

    def _expiry_state(self, expires_at: datetime | None, now: datetime) -> ExpiryState:
        if expires_at is None:
            return ExpiryState.UNKNOWN
        if expires_at <= now:
            return ExpiryState.EXPIRED
        if expires_at - now <= timedelta(seconds=self._renewal_window):
            return ExpiryState.EXPIRING
        return ExpiryState.VALID
