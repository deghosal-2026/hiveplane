"""Drift, quarantine, and schedule storage (M34-05)."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.drift.models import (
    DriftAssessment,
    DriftSchedule,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    DriftAssessmentRow,
    DriftScheduleRow,
    QuarantineRow,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class DriftStore(Protocol):
    """Storage interface for drift assessments, quarantines, and schedules."""

    def add_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def save_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_quarantine(
        self, quarantine_id: str, *, ctx: TenantContext = ...
    ) -> QuarantineRecord | None: ...

    def list_quarantines(
        self,
        *,
        workload: str | None = None,
        status: QuarantineStatus | None = None,
        ctx: TenantContext = ...,
    ) -> list[QuarantineRecord]: ...

    def active_quarantine(
        self, workload: str, *, ctx: TenantContext = ...
    ) -> QuarantineRecord | None: ...

    def add_assessment(
        self, assessment: DriftAssessment, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_assessments(
        self, workload: str, *, limit: int | None = None, ctx: TenantContext = ...
    ) -> list[DriftAssessment]: ...

    def consecutive_failures(self, workload: str, *, ctx: TenantContext = ...) -> int: ...

    def save_schedule(
        self, schedule: DriftSchedule, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_schedules(self, *, ctx: TenantContext = ...) -> list[DriftSchedule]: ...

    def clear(self) -> None: ...


def _trailing_exceeded(assessments: Sequence[DriftAssessment]) -> int:
    """Count the trailing run of exceeding assessments (newest last)."""
    count = 0
    for assessment in reversed(assessments):
        if not assessment.exceeded:
            break
        count += 1
    return count


class InMemoryDriftStore:
    """A process-local, thread-safe drift store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quarantines: dict[str, tuple[QuarantineRecord, str]] = {}
        self._assessments: list[tuple[DriftAssessment, str]] = []
        self._schedules: dict[str, tuple[DriftSchedule, str]] = {}

    def add_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._quarantines[record.quarantine_id] = (
                record.model_copy(deep=True),
                record.tenant_id,
            )

    def save_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self.add_quarantine(record, ctx=ctx)

    def get_quarantine(
        self, quarantine_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> QuarantineRecord | None:
        with self._lock:
            entry = self._quarantines.get(quarantine_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_quarantines(
        self,
        *,
        workload: str | None = None,
        status: QuarantineStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[QuarantineRecord]:
        with self._lock:
            records = [
                record
                for record, tenant in self._quarantines.values()
                if ctx.scopes(tenant)
                and (workload is None or record.workload == workload)
                and (status is None or record.status is status)
            ]
        records.sort(key=lambda record: (record.timestamp, record.quarantine_id))
        return records

    def active_quarantine(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> QuarantineRecord | None:
        active = self.list_quarantines(
            workload=workload, status=QuarantineStatus.ACTIVE, ctx=ctx
        )
        return active[-1] if active else None

    def add_assessment(
        self, assessment: DriftAssessment, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(assessment.tenant_id)
        with self._lock:
            self._assessments.append((assessment.model_copy(deep=True), assessment.tenant_id))

    def list_assessments(
        self, workload: str, *, limit: int | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DriftAssessment]:
        with self._lock:
            items = [
                assessment
                for assessment, tenant in self._assessments
                if ctx.scopes(tenant) and assessment.workload == workload
            ]
        items.sort(key=lambda item: (item.timestamp, item.workload))
        if limit is not None:
            items = items[-limit:]
        return items

    def consecutive_failures(self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> int:
        return _trailing_exceeded(self.list_assessments(workload, ctx=ctx))

    def save_schedule(
        self, schedule: DriftSchedule, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(schedule.tenant_id)
        with self._lock:
            self._schedules[schedule.schedule_id] = (
                schedule.model_copy(deep=True),
                schedule.tenant_id,
            )

    def list_schedules(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[DriftSchedule]:
        with self._lock:
            items = [
                schedule
                for schedule, tenant in self._schedules.values()
                if ctx.scopes(tenant)
            ]
        items.sort(key=lambda item: (item.next_re_cert_run, item.workload))
        return items

    def clear(self) -> None:
        with self._lock:
            self._quarantines.clear()
            self._assessments.clear()
            self._schedules.clear()


class PostgresDriftStore:
    """A durable drift store backed by PostgreSQL (M34-05)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(QuarantineRow, record.quarantine_id)
            if row is None:
                session.add(
                    QuarantineRow(
                        quarantine_id=record.quarantine_id,
                        workload=record.workload,
                        status=record.status.value,
                        actor=record.actor,
                        created_at=record.timestamp,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify quarantine {record.quarantine_id!r}"
                    )
                row.status = record.status.value
                row.payload = payload

    def save_quarantine(
        self, record: QuarantineRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self.add_quarantine(record, ctx=ctx)

    def get_quarantine(
        self, quarantine_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> QuarantineRecord | None:
        with self._session() as session:
            row = session.get(QuarantineRow, quarantine_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return QuarantineRecord.model_validate(row.payload)

    def list_quarantines(
        self,
        *,
        workload: str | None = None,
        status: QuarantineStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[QuarantineRecord]:
        statement = select(QuarantineRow)
        if workload is not None:
            statement = statement.where(QuarantineRow.workload == workload)
        if status is not None:
            statement = statement.where(QuarantineRow.status == status.value)
        if not ctx.is_system:
            statement = statement.where(QuarantineRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(QuarantineRow.created_at, QuarantineRow.quarantine_id)
        with self._session() as session:
            return [
                QuarantineRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def active_quarantine(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> QuarantineRecord | None:
        active = self.list_quarantines(
            workload=workload, status=QuarantineStatus.ACTIVE, ctx=ctx
        )
        return active[-1] if active else None

    def add_assessment(
        self, assessment: DriftAssessment, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(assessment.tenant_id)
        with self._session.begin() as session:
            session.add(
                DriftAssessmentRow(
                    workload=assessment.workload,
                    verdict=assessment.verdict.value,
                    exceeded=assessment.exceeded,
                    created_at=assessment.timestamp,
                    tenant_id=assessment.tenant_id,
                    payload=assessment.model_dump(mode="json"),
                )
            )

    def list_assessments(
        self, workload: str, *, limit: int | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DriftAssessment]:
        statement = select(DriftAssessmentRow).where(DriftAssessmentRow.workload == workload)
        if not ctx.is_system:
            statement = statement.where(DriftAssessmentRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(DriftAssessmentRow.created_at, DriftAssessmentRow.id)
        with self._session() as session:
            items = [
                DriftAssessment.model_validate(row.payload) for row in session.scalars(statement)
            ]
        if limit is not None:
            items = items[-limit:]
        return items

    def consecutive_failures(self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> int:
        return _trailing_exceeded(self.list_assessments(workload, ctx=ctx))

    def save_schedule(
        self, schedule: DriftSchedule, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(schedule.tenant_id)
        payload = schedule.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(DriftScheduleRow, schedule.schedule_id)
            if row is None:
                session.add(
                    DriftScheduleRow(
                        schedule_id=schedule.schedule_id,
                        workload=schedule.workload,
                        next_re_cert_run=schedule.next_re_cert_run,
                        status=schedule.status,
                        tenant_id=schedule.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify schedule {schedule.schedule_id!r}"
                    )
                row.next_re_cert_run = schedule.next_re_cert_run
                row.status = schedule.status
                row.payload = payload

    def list_schedules(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[DriftSchedule]:
        statement = select(DriftScheduleRow)
        if not ctx.is_system:
            statement = statement.where(DriftScheduleRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            DriftScheduleRow.next_re_cert_run, DriftScheduleRow.workload
        )
        with self._session() as session:
            return [DriftSchedule.model_validate(row.payload) for row in session.scalars(statement)]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(QuarantineRow))
            session.execute(delete(DriftAssessmentRow))
            session.execute(delete(DriftScheduleRow))


def build_drift_store(settings: Settings | None = None) -> DriftStore:
    """Build the configured drift store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresDriftStore(create_engine_from_settings(resolved))
    return InMemoryDriftStore()
