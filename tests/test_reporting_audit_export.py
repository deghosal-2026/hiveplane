"""Unit tests for audit export and its integrity proof (M57-03)."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from hiveplane.persistence.audit import (
    AuditLog,
    AuditRecord,
    InMemoryAuditLog,
)
from hiveplane.reporting.audit_export import AuditExportService
from hiveplane.reporting.errors import ReportingError
from hiveplane.reporting.models import AuditExportFormat
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext, context_for_run

_START = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
_END = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _Rig:
    service: AuditExportService
    audit: InMemoryAuditLog
    reporting: InMemoryReportingStore
    clock: _Clock


def _rig() -> _Rig:
    clock = _Clock(_START)
    audit = InMemoryAuditLog(clock=clock)
    reporting = InMemoryReportingStore()
    service = AuditExportService(reporting, audit, clock=clock)
    return _Rig(service, audit, reporting, clock)


def _seed(
    rig: _Rig,
    tenant_id: str,
    count: int,
    *,
    start: datetime = _START,
    step: int = 60,
) -> list[AuditRecord]:
    ctx = context_for_run(tenant_id)
    records: list[AuditRecord] = []
    rig.clock.now = start
    for index in range(count):
        rig.clock.now = start + timedelta(seconds=index * step)
        records.append(
            rig.audit.append(
                f"actor-{index}", f"action.{index}", f"subject-{index}", ctx=ctx
            )
        )
    return records


def test_export_csv_round_trips_and_persists() -> None:
    rig = _rig()
    records = _seed(rig, "acme", 3)
    service = rig.service
    service.bind_audit(rig.audit)

    export = service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.CSV,
    )

    assert export.record_count == 3
    assert export.tenant_id == "acme"
    assert export.format is AuditExportFormat.CSV
    assert export.integrity_proof.leaf_count == 3
    assert export.integrity_proof.first_sequence == 0
    assert export.integrity_proof.last_sequence == 2
    assert export.integrity_proof.chain_head == records[-1].hash
    assert len(export.integrity_proof.merkle_root) == 64
    assert service.verify(export) is True

    header = export.content.splitlines()[0]
    assert header == "sequence,actor,action,subject,created_at,detail,prev_hash,hash,tenant_id"
    for record in records:
        assert record.hash in export.content

    stored = rig.reporting.list_exports(ctx=context_for_run("acme"))
    assert [item.export_id for item in stored] == [export.export_id]
    assert rig.reporting.get_export(export.export_id, ctx=context_for_run("acme")) == export

    entries = rig.audit.records(ctx=context_for_run("acme"))
    assert any(entry.action == "audit.exported" for entry in entries)


def test_export_json_round_trips() -> None:
    rig = _rig()
    _seed(rig, "acme", 2)

    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )

    payload = json.loads(export.content)
    assert len(payload) == 2
    assert all("hash" in row and "prev_hash" in row for row in payload)
    assert isinstance(payload[0]["sequence"], int)
    assert rig.service.verify(export) is True


def test_export_empty_period_verifies() -> None:
    rig = _rig()

    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )

    assert export.record_count == 0
    assert export.integrity_proof.leaf_count == 0
    assert export.integrity_proof.merkle_root == "0" * 64
    assert export.integrity_proof.chain_head == "0" * 64
    assert export.integrity_proof.first_sequence is None
    assert export.integrity_proof.last_sequence is None
    assert rig.service.verify(export) is True


def test_period_filter_includes_only_in_range_prefix() -> None:
    rig = _rig()
    _seed(rig, "acme", 2)
    later = _START + timedelta(hours=1)
    rig.clock.now = later
    rig.audit.append("late", "action.late", "subject-late", ctx=context_for_run("acme"))

    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_START + timedelta(minutes=2),
        format=AuditExportFormat.JSON,
    )

    assert export.record_count == 2
    assert "late" not in export.content
    assert rig.service.verify(export) is True


def test_cross_tenant_export_excludes_other_tenants() -> None:
    rig = _rig()
    _seed(rig, "acme", 2)
    rig.clock.now = _START + timedelta(hours=1)
    rig.audit.append("beta-actor", "beta.action", "beta-subject", ctx=context_for_run("beta"))

    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.CSV,
    )

    assert export.record_count == 2
    assert "beta-actor" not in export.content
    assert "beta-subject" not in export.content
    assert rig.service.verify(export) is True


def test_verify_detects_tampered_actor_in_json() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    payload = json.loads(export.content)
    payload[0]["actor"] = "attacker"
    tampered = export.model_copy(update={"content": json.dumps(payload)})

    assert rig.service.verify(tampered) is False


def test_verify_detects_tampered_hash_in_json() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    payload = json.loads(export.content)
    payload[1]["hash"] = "0" * 64
    tampered = export.model_copy(update={"content": json.dumps(payload)})

    assert rig.service.verify(tampered) is False


def test_verify_detects_dropped_record() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    payload = json.loads(export.content)
    tampered = export.model_copy(update={"content": json.dumps(payload[:-1])})

    assert rig.service.verify(tampered) is False


def test_verify_detects_reordering() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    payload = json.loads(export.content)
    payload[0], payload[2] = payload[2], payload[0]
    tampered = export.model_copy(update={"content": json.dumps(payload)})

    assert rig.service.verify(tampered) is False


def test_verify_detects_wrong_merkle_root() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    proof = export.integrity_proof.model_copy(update={"merkle_root": "0" * 64})
    tampered = export.model_copy(update={"integrity_proof": proof})

    assert rig.service.verify(tampered) is False


def test_verify_detects_record_whose_hash_does_not_recompute() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    payload = json.loads(export.content)
    payload[1]["prev_hash"] = "f" * 64
    tampered = export.model_copy(update={"content": json.dumps(payload)})

    assert rig.service.verify(tampered) is False


def test_verify_detects_tampered_actor_in_csv() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.CSV,
    )
    reader = csv.DictReader(io.StringIO(export.content))
    rows = list(reader)
    rows[0]["actor"] = "attacker"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(reader.fieldnames or []), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    tampered = export.model_copy(update={"content": buffer.getvalue()})

    assert rig.service.verify(tampered) is False


def test_verify_detects_tampered_hash_in_csv() -> None:
    rig = _rig()
    records = _seed(rig, "acme", 3)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.CSV,
    )
    tampered = export.model_copy(
        update={"content": export.content.replace(records[0].hash, "0" * 64, 1)}
    )

    assert rig.service.verify(tampered) is False


def test_verify_rejects_malformed_content() -> None:
    rig = _rig()
    _seed(rig, "acme", 1)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )
    tampered = export.model_copy(update={"content": "not json"})

    assert rig.service.verify(tampered) is False

    not_array = export.model_copy(update={"content": "{}"})
    assert rig.service.verify(not_array) is False


def test_csv_export_round_trips_record_detail() -> None:
    rig = _rig()
    rig.clock.now = _START
    rig.audit.append(
        "alice", "run.created", "run-1", detail="tenant=acme", ctx=context_for_run("acme")
    )
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.CSV,
    )

    assert "tenant=acme" in export.content
    assert rig.service.verify(export) is True


def test_export_list_is_tenant_scoped() -> None:
    rig = _rig()
    _seed(rig, "acme", 1)
    export = rig.service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )

    assert [item.export_id for item in rig.service.list(context_for_run("acme"))] == [
        export.export_id
    ]
    assert rig.service.list(context_for_run("beta")) == []


class _BrokenAuditLog:
    def __init__(self, records: list[AuditRecord]) -> None:
        self._records = records

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditRecord:
        raise NotImplementedError

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]:
        return [
            record.model_copy(deep=True)
            for record in self._records
            if ctx.scopes(record.tenant_id)
        ]

    def prune(
        self,
        *,
        before: datetime,
        protect: Callable[[AuditRecord], bool] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> int:
        raise NotImplementedError

    def anchor(self) -> str:
        return "0" * 64

    def verify(self) -> bool:
        return False


def test_export_refuses_record_with_inconsistent_hash() -> None:
    rig = _rig()
    _seed(rig, "acme", 3)
    full = list(rig.audit.records(ctx=context_for_run("acme")))
    slice_records = [full[1], full[2].model_copy(update={"prev_hash": "0" * 64})]
    log: AuditLog = _BrokenAuditLog(slice_records)
    service = AuditExportService(rig.reporting, log, clock=rig.clock)

    with pytest.raises(ReportingError) as excinfo:
        service.export(
            context_for_run("acme"),
            period_start=_START,
            period_end=_END,
            format=AuditExportFormat.JSON,
        )

    assert "inconsistent hash" in str(excinfo.value)


def test_export_allows_interleaved_non_contiguous_window() -> None:
    rig = _rig()
    acme = context_for_run("acme")
    beta = context_for_run("beta")
    rig.clock.now = _START
    rig.audit.append("a1", "acme.one", "s1", ctx=acme)
    rig.clock.now = _START + timedelta(seconds=10)
    rig.audit.append("b1", "beta.one", "s2", ctx=beta)
    rig.clock.now = _START + timedelta(seconds=20)
    rig.audit.append("a2", "acme.two", "s3", ctx=acme)

    export = rig.service.export(
        acme,
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )

    assert export.record_count == 2
    assert "acme.one" in export.content
    assert "acme.two" in export.content
    assert "beta.one" not in export.content
    assert rig.service.verify(export) is True


def test_bind_audit_enables_auditing() -> None:
    rig = _rig()
    _seed(rig, "acme", 1)
    service = AuditExportService(rig.reporting, rig.audit, clock=rig.clock)
    service.bind_audit(rig.audit)

    service.export(
        context_for_run("acme"),
        period_start=_START,
        period_end=_END,
        format=AuditExportFormat.JSON,
    )

    entries = rig.audit.records(ctx=context_for_run("acme"))
    assert any(entry.action == "audit.exported" for entry in entries)
