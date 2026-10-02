"""Tests for the reporting store (in-memory + Postgres) (M57-01)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.delivery.models import DeliveryChannel
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.reporting.models import (
    AuditExport,
    AuditExportFormat,
    EvidenceFile,
    EvidencePack,
    IntegrityProof,
    PackSignature,
    PurgeCertificate,
    PurgeRecord,
    ReportKind,
    ReportRun,
    ReportSchedule,
    StorePurgeCount,
)
from hiveplane.reporting.store import InMemoryReportingStore, PostgresReportingStore
from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from postgres import reset_database

_NOW = datetime(2026, 9, 27, tzinfo=UTC)
_LATER = datetime(2026, 9, 28, tzinfo=UTC)
_HEX = "a" * 64
_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)
_BETA = TenantContext(tenant_id="beta", role=Role.ADMIN)


def _report(report_id: str, tenant_id: str, *, generated_at: datetime = _NOW) -> ReportRun:
    return ReportRun(
        report_id=report_id,
        tenant_id=tenant_id,
        kind=ReportKind.DIGEST,
        period_key="2026-W39",
        output_ref=f"reports/{report_id}",
        generated_at=generated_at,
    )


def _schedule(schedule_id: str, tenant_id: str) -> ReportSchedule:
    return ReportSchedule(
        schedule_id=schedule_id,
        tenant_id=tenant_id,
        team_id="platform",
        cron="0 8 * * 1",
        period_kind=CostPeriodKind.WEEK,
        channels=[DeliveryChannel.SLACK],
        created_at=_NOW,
    )


def _export(export_id: str, tenant_id: str) -> AuditExport:
    return AuditExport(
        export_id=export_id,
        tenant_id=tenant_id,
        period_start=_NOW,
        period_end=_LATER,
        format=AuditExportFormat.JSON,
        record_count=1,
        integrity_proof=IntegrityProof(
            merkle_root=_HEX, leaf_count=1, first_sequence=1, last_sequence=1, chain_head=_HEX
        ),
        content="[]",
        created_at=_NOW,
    )


def _pack(pack_id: str, tenant_id: str) -> EvidencePack:
    return EvidencePack(
        pack_id=pack_id,
        tenant_id=tenant_id,
        period_start=_NOW,
        period_end=_LATER,
        files=[
            EvidenceFile(
                name="index.json", media_type="application/json", digest=_HEX, content="{}"
            )
        ],
        signature=PackSignature(key_id="reporting", digest=_HEX, signature="sig"),
        created_at=_NOW,
    )


def _purge(purge_id: str, tenant_id: str) -> PurgeRecord:
    certificate = PurgeCertificate(
        purge_id=purge_id,
        tenant_id=tenant_id,
        scope="tenant",
        stores=[StorePurgeCount(store="runs", deleted=1)],
        total_deleted=1,
        completed_at=_NOW,
        verifier="alice",
        signature=PackSignature(key_id="reporting", digest=_HEX, signature="sig"),
    )
    return PurgeRecord(
        purge_id=purge_id,
        tenant_id=tenant_id,
        scope="tenant",
        certificate=certificate,
        completed_at=_NOW,
    )


def test_in_memory_round_trips_every_record() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    assert store.get_report("rep-a", tenant_id="acme", ctx=_ACME) == _report("rep-a", "acme")
    assert store.get_schedule("sch-a", tenant_id="acme", ctx=_ACME) == _schedule("sch-a", "acme")
    assert store.get_export("exp-a", tenant_id="acme", ctx=_ACME) == _export("exp-a", "acme")
    assert store.get_evidence("pack-a", tenant_id="acme", ctx=_ACME) == _pack("pack-a", "acme")
    assert store.get_purge("purge-a", tenant_id="acme", ctx=_ACME) == _purge("purge-a", "acme")


def test_reads_hide_foreign_tenants() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    assert store.get_report("rep-a", tenant_id="beta", ctx=_BETA) is None
    assert store.get_schedule("sch-a", tenant_id="beta", ctx=_BETA) is None
    assert store.get_export("exp-a", tenant_id="beta", ctx=_BETA) is None
    assert store.get_evidence("pack-a", tenant_id="beta", ctx=_BETA) is None
    assert store.get_purge("purge-a", tenant_id="beta", ctx=_BETA) is None

    assert store.list_reports(tenant_id="beta", ctx=_BETA) == []
    assert store.list_schedules(tenant_id="beta", ctx=_BETA) == []
    assert store.list_exports(tenant_id="beta", ctx=_BETA) == []
    assert store.list_evidence(tenant_id="beta", ctx=_BETA) == []
    assert store.list_purges(tenant_id="beta", ctx=_BETA) == []


def test_system_context_sees_all_tenants() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_report(_report("rep-b", "beta"), ctx=_BETA)
    assert len(store.list_reports(ctx=SYSTEM_CONTEXT)) == 2
    assert len(store.list_reports(tenant_id="acme", ctx=SYSTEM_CONTEXT)) == 1


def test_cross_tenant_write_raises() -> None:
    store = InMemoryReportingStore()
    with pytest.raises(TenantScopeError):
        store.save_report(_report("rep-b", "beta"), ctx=_ACME)


def test_reports_are_ordered_deterministically() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-b", "acme", generated_at=_LATER), ctx=_ACME)
    store.save_report(_report("rep-a", "acme", generated_at=_NOW), ctx=_ACME)
    assert [record.report_id for record in store.list_reports(tenant_id="acme", ctx=_ACME)] == [
        "rep-a",
        "rep-b",
    ]
    assert [
        record.report_id
        for record in store.list_reports(tenant_id="acme", kind=ReportKind.DIGEST, ctx=_ACME)
    ] == ["rep-a", "rep-b"]
    assert store.list_reports(tenant_id="acme", kind=ReportKind.PURGE, ctx=_ACME) == []


def test_clear_empties_every_store() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)
    store.clear()
    assert store.list_reports(ctx=SYSTEM_CONTEXT) == []
    assert store.list_schedules(ctx=SYSTEM_CONTEXT) == []
    assert store.list_exports(ctx=SYSTEM_CONTEXT) == []
    assert store.list_evidence(ctx=SYSTEM_CONTEXT) == []
    assert store.list_purges(ctx=SYSTEM_CONTEXT) == []


def test_build_reporting_store_defaults_to_memory() -> None:
    from hiveplane.config import Settings
    from hiveplane.reporting.store import build_reporting_store

    settings = Settings()
    assert isinstance(build_reporting_store(settings), InMemoryReportingStore)


def test_postgres_round_trips_and_scopes(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReportingStore(pg_engine)
    store.clear()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_report(_report("rep-b", "beta"), ctx=_BETA)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    assert store.get_report("rep-a", tenant_id="acme", ctx=_ACME) == _report("rep-a", "acme")
    assert store.get_report("rep-a", tenant_id="beta", ctx=_BETA) is None
    assert [r.report_id for r in store.list_reports(tenant_id="acme", ctx=_ACME)] == ["rep-a"]
    assert store.get_schedule("sch-a", tenant_id="acme", ctx=_ACME) == _schedule("sch-a", "acme")
    assert store.get_export("exp-a", tenant_id="acme", ctx=_ACME) == _export("exp-a", "acme")
    assert store.get_evidence("pack-a", tenant_id="acme", ctx=_ACME) == _pack("pack-a", "acme")
    assert store.get_purge("purge-a", tenant_id="acme", ctx=_ACME) == _purge("purge-a", "acme")
    assert len(store.list_reports(ctx=SYSTEM_CONTEXT)) == 2
    store.clear()
    assert store.list_reports(ctx=SYSTEM_CONTEXT) == []


def test_no_access_reads_are_hidden() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    assert store.get_report("rep-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_schedule("sch-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_export("exp-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_evidence("pack-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_purge("purge-a", tenant_id="beta", ctx=_ACME) is None

    assert store.list_reports(tenant_id="beta", ctx=_ACME) == []
    assert store.list_schedules(tenant_id="beta", ctx=_ACME) == []
    assert store.list_exports(tenant_id="beta", ctx=_ACME) == []
    assert store.list_evidence(tenant_id="beta", ctx=_ACME) == []
    assert store.list_purges(tenant_id="beta", ctx=_ACME) == []
    assert store.get_report("rep-a", tenant_id="acme", ctx=SYSTEM_CONTEXT) is not None


def test_postgres_updates_and_no_access(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReportingStore(pg_engine)
    store.clear()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_report(_report("rep-a", "acme", generated_at=_LATER), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    found = store.get_report("rep-a", tenant_id="acme", ctx=_ACME)
    assert found is not None and found.generated_at == _LATER
    assert store.get_report("rep-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_report("rep-a", tenant_id="acme", ctx=SYSTEM_CONTEXT) is not None
    assert store.get_report("rep-a", tenant_id="beta", ctx=SYSTEM_CONTEXT) is None
    assert store.list_reports(tenant_id="beta", ctx=_ACME) == []
    assert store.list_schedules(tenant_id="beta", ctx=_ACME) == []
    assert store.list_exports(tenant_id="beta", ctx=_ACME) == []
    assert store.list_evidence(tenant_id="beta", ctx=_ACME) == []
    assert store.list_purges(tenant_id="beta", ctx=_ACME) == []
    assert store.get_schedule("sch-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_export("exp-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_evidence("pack-a", tenant_id="beta", ctx=_ACME) is None
    assert store.get_purge("purge-a", tenant_id="beta", ctx=_ACME) is None


def test_build_reporting_store_postgres() -> None:
    from hiveplane.config import ExecutionSettings, Settings
    from hiveplane.reporting.store import build_reporting_store

    settings = Settings(execution=ExecutionSettings(store="postgres"))
    assert isinstance(build_reporting_store(settings), PostgresReportingStore)


def test_postgres_read_paths(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReportingStore(pg_engine)
    store.clear()
    store.save_report(_report("rep-a", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("sch-a", "acme"), ctx=_ACME)
    store.save_export(_export("exp-a", "acme"), ctx=_ACME)
    store.save_evidence(_pack("pack-a", "acme"), ctx=_ACME)
    store.save_purge(_purge("purge-a", "acme"), ctx=_ACME)

    assert [r.schedule_id for r in store.list_schedules(tenant_id="acme", ctx=_ACME)] == ["sch-a"]
    assert [e.export_id for e in store.list_exports(tenant_id="acme", ctx=_ACME)] == ["exp-a"]
    assert [p.pack_id for p in store.list_evidence(tenant_id="acme", ctx=_ACME)] == ["pack-a"]
    assert [p.purge_id for p in store.list_purges(tenant_id="acme", ctx=_ACME)] == ["purge-a"]
    assert len(store.list_schedules(ctx=SYSTEM_CONTEXT)) == 1
    assert len(store.list_exports(ctx=SYSTEM_CONTEXT)) == 1
    assert len(store.list_evidence(ctx=SYSTEM_CONTEXT)) == 1
    assert len(store.list_purges(ctx=SYSTEM_CONTEXT)) == 1
    assert store.list_reports(tenant_id="acme", kind=ReportKind.PURGE, ctx=_ACME) == []
    assert store.get_report("missing", tenant_id="acme", ctx=_ACME) is None
    assert store.get_schedule("missing", tenant_id="acme", ctx=_ACME) is None
    assert store.get_export("missing", tenant_id="acme", ctx=_ACME) is None
    assert store.get_evidence("missing", tenant_id="acme", ctx=_ACME) is None
    assert store.get_purge("missing", tenant_id="acme", ctx=_ACME) is None
    assert store.get_report("missing", tenant_id="beta", ctx=_ACME) is None


def test_reused_id_by_foreign_tenant_is_rejected() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report("shared", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("shared", "acme"), ctx=_ACME)
    store.save_export(_export("shared", "acme"), ctx=_ACME)
    store.save_evidence(_pack("shared", "acme"), ctx=_ACME)
    store.save_purge(_purge("shared", "acme"), ctx=_ACME)

    with pytest.raises(TenantScopeError):
        store.save_report(_report("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_schedule(_schedule("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_export(_export("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_evidence(_pack("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_purge(_purge("shared", "beta"), ctx=_BETA)

    assert store.get_report("shared", tenant_id="acme", ctx=_ACME) == _report("shared", "acme")
    assert store.get_schedule("shared", tenant_id="acme", ctx=_ACME) == _schedule("shared", "acme")
    assert store.get_export("shared", tenant_id="acme", ctx=_ACME) == _export("shared", "acme")
    assert store.get_evidence("shared", tenant_id="acme", ctx=_ACME) == _pack("shared", "acme")
    assert store.get_purge("shared", tenant_id="acme", ctx=_ACME) == _purge("shared", "acme")
    assert store.get_report("shared", tenant_id="beta", ctx=_BETA) is None


def test_postgres_reused_id_by_foreign_tenant_is_rejected(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReportingStore(pg_engine)
    store.clear()
    store.save_report(_report("shared", "acme"), ctx=_ACME)
    store.save_schedule(_schedule("shared", "acme"), ctx=_ACME)
    store.save_export(_export("shared", "acme"), ctx=_ACME)
    store.save_evidence(_pack("shared", "acme"), ctx=_ACME)
    store.save_purge(_purge("shared", "acme"), ctx=_ACME)

    with pytest.raises(TenantScopeError):
        store.save_report(_report("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_schedule(_schedule("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_export(_export("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_evidence(_pack("shared", "beta"), ctx=_BETA)
    with pytest.raises(TenantScopeError):
        store.save_purge(_purge("shared", "beta"), ctx=_BETA)

    assert store.get_report("shared", tenant_id="acme", ctx=_ACME) == _report("shared", "acme")
    assert store.get_schedule("shared", tenant_id="acme", ctx=_ACME) == _schedule("shared", "acme")
    assert store.get_export("shared", tenant_id="acme", ctx=_ACME) == _export("shared", "acme")
    assert store.get_evidence("shared", tenant_id="acme", ctx=_ACME) == _pack("shared", "acme")
    assert store.get_purge("shared", tenant_id="acme", ctx=_ACME) == _purge("shared", "acme")
    assert store.get_report("shared", tenant_id="beta", ctx=_BETA) is None
