"""Reporting and compliance errors (M57)."""

from __future__ import annotations


class ReportingError(Exception):
    """Base class for reporting failures."""


class ReportNotFoundError(ReportingError):
    """Raised when a report run does not exist in the acting tenant."""

    def __init__(self, report_id: str) -> None:
        self.report_id = report_id
        super().__init__(f"report not found: {report_id!r}")


class AuditExportNotFoundError(ReportingError):
    """Raised when an audit export does not exist in the acting tenant."""

    def __init__(self, export_id: str) -> None:
        self.export_id = export_id
        super().__init__(f"audit export not found: {export_id!r}")


class EvidencePackNotFoundError(ReportingError):
    """Raised when an evidence pack does not exist in the acting tenant."""

    def __init__(self, pack_id: str) -> None:
        self.pack_id = pack_id
        super().__init__(f"evidence pack not found: {pack_id!r}")


class PurgeIncompleteError(ReportingError):
    """Raised when a purge cannot certify completeness (e.g. legal hold)."""

    def __init__(self, tenant_id: str, detail: str) -> None:
        self.tenant_id = tenant_id
        self.detail = detail
        super().__init__(f"purge incomplete for {tenant_id!r}: {detail}")
