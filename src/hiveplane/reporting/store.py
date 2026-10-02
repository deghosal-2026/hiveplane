"""Persistence for reporting runs, schedules, exports, evidence, and purges (M57)."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import CursorResult

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    AuditExportRow,
    EvidencePackRow,
    PurgeRecordRow,
    ReportRunRow,
    ReportScheduleRow,
)
from hiveplane.reporting.models import (
    AuditExport,
    EvidencePack,
    PurgeRecord,
    ReportKind,
    ReportRun,
    ReportSchedule,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


def _reuse_guard(record_id: str, owner: str, incoming: str, kind: str) -> None:
    """Raise when an id owned by one tenant is reused by another."""
    if owner != incoming:
        raise TenantScopeError(
            incoming, f"{kind} {record_id!r} already belongs to tenant {owner!r}"
        )


def _read_tenant(ctx: TenantContext, tenant_id: str | None) -> tuple[bool, str | None]:
    """Resolve ``(allowed, tenant)`` for a read; ``tenant=None`` means all tenants."""
    if ctx.is_system:
        return True, tenant_id
    target = tenant_id or ctx.tenant_id
    if ctx.scopes(target):
        return True, target
    return False, None


class ReportingStore(Protocol):
    """Storage interface for reporting, compliance, and purge records."""

    def save_report(self, report: ReportRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get_report(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportRun | None: ...

    def list_reports(
        self,
        *,
        tenant_id: str | None = None,
        kind: ReportKind | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReportRun]: ...

    def save_schedule(
        self, schedule: ReportSchedule, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_schedule(
        self,
        schedule_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportSchedule | None: ...

    def list_schedules(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ReportSchedule]: ...

    def claim_schedule(
        self,
        schedule_id: str,
        *,
        expected_next_run: datetime,
        new_next_run: datetime | None,
        active: bool,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool: ...

    def save_export(self, export: AuditExport, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get_export(
        self,
        export_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditExport | None: ...

    def list_exports(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AuditExport]: ...

    def save_evidence(
        self, pack: EvidencePack, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_evidence(
        self,
        pack_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EvidencePack | None: ...

    def list_evidence(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EvidencePack]: ...

    def save_purge(self, record: PurgeRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get_purge(
        self,
        purge_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PurgeRecord | None: ...

    def list_purges(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PurgeRecord]: ...

    def purge_tenant_reports(self, tenant_id: str) -> int: ...

    def clear(self) -> None: ...


class InMemoryReportingStore:
    """A process-local reporting store with explicit tenant scoping."""

    def __init__(self) -> None:
        self._reports: dict[str, ReportRun] = {}
        self._schedules: dict[str, ReportSchedule] = {}
        self._exports: dict[str, AuditExport] = {}
        self._evidence: dict[str, EvidencePack] = {}
        self._purges: dict[str, PurgeRecord] = {}
        self._schedule_lock = threading.RLock()

    def save_report(self, report: ReportRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(report.tenant_id)
        existing = self._reports.get(report.report_id)
        if existing is not None:
            _reuse_guard(report.report_id, existing.tenant_id, report.tenant_id, "ReportRun")
        self._reports[report.report_id] = report.model_copy(deep=True)

    def get_report(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportRun | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        record = self._reports.get(report_id)
        if record is None or (target is not None and record.tenant_id != target):
            return None
        return record.model_copy(deep=True)

    def list_reports(
        self,
        *,
        tenant_id: str | None = None,
        kind: ReportKind | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReportRun]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        records = [
            record.model_copy(deep=True)
            for record in self._reports.values()
            if (target is None or record.tenant_id == target)
            and (kind is None or record.kind == kind)
        ]
        records.sort(key=lambda record: (record.generated_at, record.report_id))
        return records

    def save_schedule(
        self, schedule: ReportSchedule, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(schedule.tenant_id)
        with self._schedule_lock:
            existing = self._schedules.get(schedule.schedule_id)
            if existing is not None:
                _reuse_guard(
                    schedule.schedule_id,
                    existing.tenant_id,
                    schedule.tenant_id,
                    "ReportSchedule",
                )
            self._schedules[schedule.schedule_id] = schedule.model_copy(deep=True)

    def get_schedule(
        self,
        schedule_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportSchedule | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._schedule_lock:
            record = self._schedules.get(schedule_id)
            if record is None or (target is not None and record.tenant_id != target):
                return None
            return record.model_copy(deep=True)

    def list_schedules(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ReportSchedule]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        with self._schedule_lock:
            records = [
                record.model_copy(deep=True)
                for record in self._schedules.values()
                if target is None or record.tenant_id == target
            ]
        records.sort(key=lambda record: record.schedule_id)
        return records

    def claim_schedule(
        self,
        schedule_id: str,
        *,
        expected_next_run: datetime,
        new_next_run: datetime | None,
        active: bool,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool:
        """Atomically advance a schedule iff it still matches ``expected_next_run``."""
        with self._schedule_lock:
            record = self._schedules.get(schedule_id)
            if record is None or not ctx.scopes(record.tenant_id):
                return False
            if record.next_run != expected_next_run:
                return False
            self._schedules[schedule_id] = record.model_copy(
                update={"next_run": new_next_run, "active": active}
            )
            return True

    def save_export(self, export: AuditExport, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(export.tenant_id)
        existing = self._exports.get(export.export_id)
        if existing is not None:
            _reuse_guard(export.export_id, existing.tenant_id, export.tenant_id, "AuditExport")
        self._exports[export.export_id] = export.model_copy(deep=True)

    def get_export(
        self,
        export_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditExport | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        record = self._exports.get(export_id)
        if record is None or (target is not None and record.tenant_id != target):
            return None
        return record.model_copy(deep=True)

    def list_exports(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AuditExport]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        records = [
            record.model_copy(deep=True)
            for record in self._exports.values()
            if target is None or record.tenant_id == target
        ]
        records.sort(key=lambda record: (record.created_at, record.export_id))
        return records

    def save_evidence(self, pack: EvidencePack, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(pack.tenant_id)
        existing = self._evidence.get(pack.pack_id)
        if existing is not None:
            _reuse_guard(pack.pack_id, existing.tenant_id, pack.tenant_id, "EvidencePack")
        self._evidence[pack.pack_id] = pack.model_copy(deep=True)

    def get_evidence(
        self,
        pack_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EvidencePack | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        record = self._evidence.get(pack_id)
        if record is None or (target is not None and record.tenant_id != target):
            return None
        return record.model_copy(deep=True)

    def list_evidence(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EvidencePack]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        records = [
            record.model_copy(deep=True)
            for record in self._evidence.values()
            if target is None or record.tenant_id == target
        ]
        records.sort(key=lambda record: (record.created_at, record.pack_id))
        return records

    def save_purge(self, record: PurgeRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(record.tenant_id)
        existing = self._purges.get(record.purge_id)
        if existing is not None:
            _reuse_guard(record.purge_id, existing.tenant_id, record.tenant_id, "PurgeRecord")
        self._purges[record.purge_id] = record.model_copy(deep=True)

    def get_purge(
        self,
        purge_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PurgeRecord | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        record = self._purges.get(purge_id)
        if record is None or (target is not None and record.tenant_id != target):
            return None
        return record.model_copy(deep=True)

    def list_purges(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PurgeRecord]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        records = [
            record.model_copy(deep=True)
            for record in self._purges.values()
            if target is None or record.tenant_id == target
        ]
        records.sort(key=lambda record: (record.completed_at, record.purge_id))
        return records

    def purge_tenant_reports(self, tenant_id: str) -> int:
        report_ids = [
            key for key, value in self._reports.items() if value.tenant_id == tenant_id
        ]
        for key in report_ids:
            del self._reports[key]
        schedule_ids = [
            key
            for key, value in self._schedules.items()
            if value.tenant_id == tenant_id
        ]
        for key in schedule_ids:
            del self._schedules[key]
        export_ids = [
            key for key, value in self._exports.items() if value.tenant_id == tenant_id
        ]
        for key in export_ids:
            del self._exports[key]
        evidence_ids = [
            key for key, value in self._evidence.items() if value.tenant_id == tenant_id
        ]
        for key in evidence_ids:
            del self._evidence[key]
        return (
            len(report_ids)
            + len(schedule_ids)
            + len(export_ids)
            + len(evidence_ids)
        )

    def clear(self) -> None:
        self._reports.clear()
        self._schedules.clear()
        self._exports.clear()
        self._evidence.clear()
        self._purges.clear()


class PostgresReportingStore:
    """A durable reporting store backed by PostgreSQL (M57)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_report(self, report: ReportRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(report.tenant_id)
        with self._session.begin() as session:
            row = session.get(ReportRunRow, report.report_id)
            payload = report.model_dump(mode="json")
            if row is None:
                session.add(
                    ReportRunRow(
                        report_id=report.report_id,
                        kind=report.kind.value,
                        generated_at=report.generated_at,
                        tenant_id=report.tenant_id,
                        payload=payload,
                    )
                )
            else:
                _reuse_guard(report.report_id, row.tenant_id, report.tenant_id, "ReportRun")
                row.kind = report.kind.value
                row.generated_at = report.generated_at
                row.tenant_id = report.tenant_id
                row.payload = payload

    def get_report(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportRun | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._session() as session:
            row = session.get(ReportRunRow, report_id)
            if row is None or (target is not None and row.tenant_id != target):
                return None
            return ReportRun.model_validate(row.payload)

    def list_reports(
        self,
        *,
        tenant_id: str | None = None,
        kind: ReportKind | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReportRun]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        statement = select(ReportRunRow)
        if target is not None:
            statement = statement.where(ReportRunRow.tenant_id == target)
        if kind is not None:
            statement = statement.where(ReportRunRow.kind == kind.value)
        statement = statement.order_by(ReportRunRow.generated_at, ReportRunRow.report_id)
        with self._session() as session:
            return [ReportRun.model_validate(row.payload) for row in session.scalars(statement)]

    def save_schedule(
        self, schedule: ReportSchedule, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(schedule.tenant_id)
        with self._session.begin() as session:
            row = session.get(ReportScheduleRow, schedule.schedule_id)
            payload = schedule.model_dump(mode="json")
            if row is None:
                session.add(
                    ReportScheduleRow(
                        schedule_id=schedule.schedule_id,
                        active=schedule.active,
                        next_run=schedule.next_run,
                        created_at=schedule.created_at,
                        tenant_id=schedule.tenant_id,
                        payload=payload,
                    )
                )
            else:
                _reuse_guard(
                    schedule.schedule_id, row.tenant_id, schedule.tenant_id, "ReportSchedule"
                )
                row.active = schedule.active
                row.next_run = schedule.next_run
                row.created_at = schedule.created_at
                row.tenant_id = schedule.tenant_id
                row.payload = payload

    def get_schedule(
        self,
        schedule_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ReportSchedule | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._session() as session:
            row = session.get(ReportScheduleRow, schedule_id)
            if row is None or (target is not None and row.tenant_id != target):
                return None
            return ReportSchedule.model_validate(row.payload)

    def list_schedules(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ReportSchedule]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        statement = select(ReportScheduleRow)
        if target is not None:
            statement = statement.where(ReportScheduleRow.tenant_id == target)
        statement = statement.order_by(ReportScheduleRow.schedule_id)
        with self._session() as session:
            return [
                ReportSchedule.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def claim_schedule(
        self,
        schedule_id: str,
        *,
        expected_next_run: datetime,
        new_next_run: datetime | None,
        active: bool,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool:
        """Lock the row, then advance it iff it still matches ``expected_next_run``."""
        with self._session.begin() as session:
            row = session.get(ReportScheduleRow, schedule_id, with_for_update=True)
            if row is None or not ctx.scopes(row.tenant_id):
                return False
            record = ReportSchedule.model_validate(row.payload)
            if record.next_run != expected_next_run:
                return False
            updated = record.model_copy(
                update={"next_run": new_next_run, "active": active}
            )
            row.active = updated.active
            row.next_run = updated.next_run
            row.payload = updated.model_dump(mode="json")
            return True

    def save_export(self, export: AuditExport, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(export.tenant_id)
        with self._session.begin() as session:
            row = session.get(AuditExportRow, export.export_id)
            payload = export.model_dump(mode="json")
            if row is None:
                session.add(
                    AuditExportRow(
                        export_id=export.export_id,
                        format=export.format.value,
                        created_at=export.created_at,
                        tenant_id=export.tenant_id,
                        payload=payload,
                    )
                )
            else:
                _reuse_guard(export.export_id, row.tenant_id, export.tenant_id, "AuditExport")
                row.format = export.format.value
                row.created_at = export.created_at
                row.tenant_id = export.tenant_id
                row.payload = payload

    def get_export(
        self,
        export_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditExport | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._session() as session:
            row = session.get(AuditExportRow, export_id)
            if row is None or (target is not None and row.tenant_id != target):
                return None
            return AuditExport.model_validate(row.payload)

    def list_exports(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AuditExport]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        statement = select(AuditExportRow)
        if target is not None:
            statement = statement.where(AuditExportRow.tenant_id == target)
        statement = statement.order_by(AuditExportRow.created_at, AuditExportRow.export_id)
        with self._session() as session:
            return [AuditExport.model_validate(row.payload) for row in session.scalars(statement)]

    def save_evidence(self, pack: EvidencePack, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(pack.tenant_id)
        with self._session.begin() as session:
            row = session.get(EvidencePackRow, pack.pack_id)
            payload = pack.model_dump(mode="json")
            if row is None:
                session.add(
                    EvidencePackRow(
                        pack_id=pack.pack_id,
                        created_at=pack.created_at,
                        tenant_id=pack.tenant_id,
                        payload=payload,
                    )
                )
            else:
                _reuse_guard(pack.pack_id, row.tenant_id, pack.tenant_id, "EvidencePack")
                row.created_at = pack.created_at
                row.tenant_id = pack.tenant_id
                row.payload = payload

    def get_evidence(
        self,
        pack_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EvidencePack | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._session() as session:
            row = session.get(EvidencePackRow, pack_id)
            if row is None or (target is not None and row.tenant_id != target):
                return None
            return EvidencePack.model_validate(row.payload)

    def list_evidence(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EvidencePack]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        statement = select(EvidencePackRow)
        if target is not None:
            statement = statement.where(EvidencePackRow.tenant_id == target)
        statement = statement.order_by(EvidencePackRow.created_at, EvidencePackRow.pack_id)
        with self._session() as session:
            return [EvidencePack.model_validate(row.payload) for row in session.scalars(statement)]

    def save_purge(self, record: PurgeRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(record.tenant_id)
        with self._session.begin() as session:
            row = session.get(PurgeRecordRow, record.purge_id)
            payload = record.model_dump(mode="json")
            if row is None:
                session.add(
                    PurgeRecordRow(
                        purge_id=record.purge_id,
                        scope=record.scope,
                        completed_at=record.completed_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                _reuse_guard(record.purge_id, row.tenant_id, record.tenant_id, "PurgeRecord")
                row.scope = record.scope
                row.completed_at = record.completed_at
                row.tenant_id = record.tenant_id
                row.payload = payload

    def get_purge(
        self,
        purge_id: str,
        *,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PurgeRecord | None:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return None
        with self._session() as session:
            row = session.get(PurgeRecordRow, purge_id)
            if row is None or (target is not None and row.tenant_id != target):
                return None
            return PurgeRecord.model_validate(row.payload)

    def list_purges(
        self, *, tenant_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PurgeRecord]:
        allowed, target = _read_tenant(ctx, tenant_id)
        if not allowed:
            return []
        statement = select(PurgeRecordRow)
        if target is not None:
            statement = statement.where(PurgeRecordRow.tenant_id == target)
        statement = statement.order_by(PurgeRecordRow.completed_at, PurgeRecordRow.purge_id)
        with self._session() as session:
            return [PurgeRecord.model_validate(row.payload) for row in session.scalars(statement)]

    def purge_tenant_reports(self, tenant_id: str) -> int:
        with self._session.begin() as session:
            total = 0
            for table in (
                ReportRunRow,
                ReportScheduleRow,
                AuditExportRow,
                EvidencePackRow,
            ):
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        delete(table).where(table.tenant_id == tenant_id)
                    ),
                )
                total += int(result.rowcount or 0)
            return total

    def clear(self) -> None:
        with self._session.begin() as session:
            for table in (
                ReportRunRow,
                ReportScheduleRow,
                AuditExportRow,
                EvidencePackRow,
                PurgeRecordRow,
            ):
                session.execute(delete(table))


def build_reporting_store(settings: Settings | None = None) -> ReportingStore:
    """Build the configured reporting store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresReportingStore(create_engine_from_settings(resolved))
    return InMemoryReportingStore()
