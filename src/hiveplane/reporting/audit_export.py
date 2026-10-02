"""Audit export with a Merkle integrity proof (M57-03)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime

from hiveplane.persistence.audit import AuditChain, AuditLog, AuditRecord
from hiveplane.reporting.errors import ReportingError
from hiveplane.reporting.merkle import merkle_root
from hiveplane.reporting.models import AuditExport, AuditExportFormat, IntegrityProof
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy.context import TenantContext

_NULL_HASH = "0" * 64
_CSV_FIELDS = (
    "sequence",
    "actor",
    "action",
    "subject",
    "created_at",
    "detail",
    "prev_hash",
    "hash",
    "tenant_id",
)


class AuditExportService:
    """Exports the acting tenant's audit range with a verifiable integrity proof."""

    def __init__(
        self,
        reporting_store: ReportingStore,
        audit_log: AuditLog,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._audit_log = audit_log
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def export(
        self,
        ctx: TenantContext,
        *,
        period_start: AwareDatetime,
        period_end: AwareDatetime,
        format: AuditExportFormat,  # noqa: A002
    ) -> AuditExport:
        """Export the tenant's records in ``[period_start, period_end]``."""
        records = [
            record
            for record in self._audit_log.records(ctx=ctx)
            if period_start <= record.created_at <= period_end
        ]
        for record in records:
            if AuditChain.compute_hash(record.prev_hash, record) != record.hash:
                raise ReportingError(
                    "refusing to export an audit record with an inconsistent "
                    f"hash at sequence {record.sequence}"
                )
        root = merkle_root([record.hash for record in records])
        proof = IntegrityProof(
            merkle_root=root,
            leaf_count=len(records),
            first_sequence=records[0].sequence if records else None,
            last_sequence=records[-1].sequence if records else None,
            chain_head=records[-1].hash if records else _NULL_HASH,
        )
        export = AuditExport(
            export_id=_export_id(ctx.tenant_id, period_start, period_end, format, root),
            tenant_id=ctx.tenant_id,
            period_start=period_start,
            period_end=period_end,
            format=format,
            record_count=len(records),
            integrity_proof=proof,
            content=_serialize(records, format),
            created_at=self._clock(),
        )
        self._reporting_store.save_export(export, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                ctx.operator_id or "operator",
                "audit.exported",
                export.export_id,
                detail=(
                    f"tenant={ctx.tenant_id} format={format.value} records={len(records)}"
                ),
                ctx=ctx,
            )
        return export

    def verify(self, export: AuditExport) -> bool:
        """Return True when ``export.content`` matches its integrity proof."""
        try:
            rows = _parse(export)
            records = [AuditRecord.model_validate(row) for row in rows]
        except (ValueError, TypeError, KeyError, csv.Error, json.JSONDecodeError):
            return False
        if any(
            AuditChain.compute_hash(record.prev_hash, record) != record.hash
            for record in records
        ):
            return False
        if len(records) != export.integrity_proof.leaf_count:
            return False
        if merkle_root([record.hash for record in records]) != export.integrity_proof.merkle_root:
            return False
        head = records[-1].hash if records else _NULL_HASH
        return head == export.integrity_proof.chain_head

    def list(self, ctx: TenantContext) -> list[AuditExport]:
        """List the acting tenant's persisted audit exports."""
        return self._reporting_store.list_exports(ctx=ctx)


def _serialize(records: list[AuditRecord], export_format: AuditExportFormat) -> str:
    if export_format is AuditExportFormat.CSV:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(_CSV_FIELDS), lineterminator="\n")
        writer.writeheader()
        for record in records:
            row = record.model_dump(mode="json")
            row["detail"] = row["detail"] or ""
            writer.writerow({name: row[name] for name in _CSV_FIELDS})
        return buffer.getvalue()
    payload = [record.model_dump(mode="json") for record in records]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _parse(export: AuditExport) -> list[dict[str, Any]]:
    if export.format is AuditExportFormat.CSV:
        reader = csv.DictReader(io.StringIO(export.content))
        rows: list[dict[str, Any]] = []
        for raw in reader:
            row: dict[str, Any] = dict(raw)
            if row.get("detail") == "":
                row["detail"] = None
            rows.append(row)
        return rows
    parsed = json.loads(export.content)
    if not isinstance(parsed, list):
        raise ValueError("audit export content must be a JSON array")
    return [dict(row) if isinstance(row, dict) else row for row in parsed]


def _export_id(
    tenant_id: str,
    period_start: AwareDatetime,
    period_end: AwareDatetime,
    export_format: AuditExportFormat,
    root: str,
) -> str:
    payload = (
        f"{tenant_id}|{period_start.isoformat()}|{period_end.isoformat()}|"
        f"{export_format.value}|{root}"
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"export-{token}"
