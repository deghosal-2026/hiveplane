"""Tests for the reporting models, settings, and RBAC (M57-01)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.auth.models import Permission, Scope
from hiveplane.auth.rbac import ROLE_PERMISSIONS, SCOPE_PERMISSIONS
from hiveplane.config import ReportingSettings
from hiveplane.delivery.models import DeliveryChannel
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.reporting.errors import (
    AuditExportNotFoundError,
    EvidencePackNotFoundError,
    PurgeIncompleteError,
    ReportingError,
    ReportNotFoundError,
)
from hiveplane.reporting.models import (
    AuditExport,
    AuditExportFormat,
    EvidenceFile,
    EvidencePack,
    IntegrityProof,
    PackSignature,
    PurgeCertificate,
    PurgeRecord,
    Redaction,
    ReportKind,
    ReportRun,
    ReportSchedule,
    RetentionDataClass,
    RetentionPurgeResult,
    ScrubResult,
    StorePurgeCount,
)
from hiveplane.tenancy import Role

_NOW = datetime(2026, 9, 27, tzinfo=UTC)
_HEX = "a" * 64


def _proof() -> IntegrityProof:
    return IntegrityProof(
        merkle_root=_HEX,
        leaf_count=3,
        first_sequence=1,
        last_sequence=3,
        chain_head="b" * 64,
    )


def _signature() -> PackSignature:
    return PackSignature(key_id="reporting", digest=_HEX, signature="sig")


def test_report_run_round_trips() -> None:
    run = ReportRun(
        report_id="rep-1",
        tenant_id="acme",
        kind=ReportKind.DIGEST,
        period_key="2026-W39",
        output_ref="digest/2026-W39.md",
        generated_at=_NOW,
    )
    assert run.kind is ReportKind.DIGEST
    assert run.tenant_id == "acme"


def test_report_run_rejects_empty_id_and_extra() -> None:
    with pytest.raises(ValidationError):
        ReportRun(
            report_id="",
            tenant_id="acme",
            kind=ReportKind.DIGEST,
            period_key="2026-W39",
            output_ref="ref",
            generated_at=_NOW,
        )
    with pytest.raises(ValidationError):
        ReportRun(
            report_id="rep-1",
            tenant_id="acme",
            kind=ReportKind.DIGEST,
            period_key="2026-W39",
            output_ref="ref",
            generated_at=_NOW,
            nope=1,  # type: ignore[call-arg]
        )


def test_integrity_proof_rejects_empty_hash() -> None:
    with pytest.raises(ValidationError):
        IntegrityProof(merkle_root="", leaf_count=0, chain_head=_HEX)
    with pytest.raises(ValidationError):
        IntegrityProof(merkle_root=_HEX, leaf_count=-1, chain_head=_HEX)


def test_audit_export_round_trips() -> None:
    export = AuditExport(
        export_id="exp-1",
        tenant_id="acme",
        period_start=_NOW,
        period_end=_NOW,
        format=AuditExportFormat.CSV,
        record_count=3,
        integrity_proof=_proof(),
        content="actor,action\nalice,approve\n",
        created_at=_NOW,
    )
    assert export.format is AuditExportFormat.CSV
    assert export.integrity_proof.leaf_count == 3


def test_audit_export_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        AuditExport(
            export_id="",
            tenant_id="acme",
            period_start=_NOW,
            period_end=_NOW,
            format=AuditExportFormat.JSON,
            record_count=0,
            integrity_proof=_proof(),
            content="[]",
            created_at=_NOW,
        )


def test_evidence_pack_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        EvidencePack(
            pack_id="",
            tenant_id="acme",
            period_start=_NOW,
            period_end=_NOW,
            files=[
                EvidenceFile(
                    name="a.json", media_type="application/json", digest=_HEX, content="{}"
                )
            ],
            signature=_signature(),
            created_at=_NOW,
        )


def test_evidence_pack_round_trips() -> None:
    pack = EvidencePack(
        pack_id="pack-1",
        tenant_id="acme",
        period_start=_NOW,
        period_end=_NOW,
        files=[
            EvidenceFile(name="a.json", media_type="application/json", digest=_HEX, content="{}")
        ],
        signature=_signature(),
        created_at=_NOW,
    )
    assert pack.files[0].name == "a.json"
    assert pack.signature.algorithm == "ed25519"


def test_purge_record_rejects_empty_id() -> None:
    certificate = PurgeCertificate(
        purge_id="purge-1",
        tenant_id="acme",
        scope="tenant",
        stores=[StorePurgeCount(store="runs", deleted=2)],
        total_deleted=2,
        completed_at=_NOW,
        verifier="alice",
        signature=_signature(),
    )
    with pytest.raises(ValidationError):
        PurgeRecord(
            purge_id="",
            tenant_id="acme",
            scope="tenant",
            certificate=certificate,
            completed_at=_NOW,
        )
    record = PurgeRecord(
        purge_id="purge-1",
        tenant_id="acme",
        scope="tenant",
        certificate=certificate,
        completed_at=_NOW,
    )
    assert record.certificate.total_deleted == 2


def test_purge_models_reject_negative_counts() -> None:
    with pytest.raises(ValidationError):
        StorePurgeCount(store="runs", deleted=-1)
    with pytest.raises(ValidationError):
        PurgeCertificate(
            purge_id="purge-1",
            tenant_id="acme",
            scope="tenant",
            stores=[],
            total_deleted=-1,
            completed_at=_NOW,
            verifier="alice",
            signature=_signature(),
        )
    result = RetentionPurgeResult(
        classes=[StorePurgeCount(store="runs", deleted=1)], completed_at=_NOW
    )
    assert result.classes[0].deleted == 1


def test_report_schedule_defaults() -> None:
    schedule = ReportSchedule(
        schedule_id="sch-1",
        tenant_id="acme",
        team_id=None,
        cron="0 8 * * 1",
        channels=[DeliveryChannel.SLACK],
        created_at=_NOW,
    )
    assert schedule.period_kind is CostPeriodKind.WEEK
    assert schedule.active is True
    assert schedule.next_run is None
    assert schedule.channels == [DeliveryChannel.SLACK]


def test_report_schedule_rejects_empty_cron() -> None:
    with pytest.raises(ValidationError):
        ReportSchedule(
            schedule_id="sch-1",
            tenant_id="acme",
            cron="",
            channels=[],
            created_at=_NOW,
        )


def test_retention_data_class_members() -> None:
    assert {member.value for member in RetentionDataClass} == {
        "runs",
        "logs",
        "artifacts",
        "audit",
        "metering",
    }


def test_scrub_result_round_trips() -> None:
    result = ScrubResult(text="hi", redactions=[Redaction(kind="email", count=1)])
    assert result.redactions[0].kind == "email"


def test_reporting_settings_defaults() -> None:
    settings = ReportingSettings()
    assert settings.enabled is False
    assert settings.digest_cron == "0 8 * * 1"
    assert settings.digest_period is CostPeriodKind.WEEK
    assert settings.pii_enabled is False
    assert settings.pii_salt == "hiveplane-pii"
    assert settings.default_retain_days == 90
    assert settings.signing_key_id == "reporting"


def test_admin_has_reports_manage() -> None:
    assert Permission.REPORTS_MANAGE in ROLE_PERMISSIONS[Role.ADMIN]
    assert SCOPE_PERMISSIONS[Scope.REPORTS_WRITE] == frozenset({Permission.REPORTS_MANAGE})
    assert Permission.REPORTS_MANAGE.value == "reports_manage"


def test_audit_export_format_members() -> None:
    assert {member.value for member in AuditExportFormat} == {"csv", "json"}


def test_reporting_errors_carry_context() -> None:
    assert isinstance(ReportNotFoundError("rep-1"), ReportingError)
    assert ReportNotFoundError("rep-1").report_id == "rep-1"
    assert AuditExportNotFoundError("exp-1").export_id == "exp-1"
    assert EvidencePackNotFoundError("pack-1").pack_id == "pack-1"
    error = PurgeIncompleteError("acme", "legal hold")
    assert error.tenant_id == "acme"
    assert "legal hold" in str(error)
    assert "rep-1" in str(ReportNotFoundError("rep-1"))
    assert "exp-1" in str(AuditExportNotFoundError("exp-1"))
    assert "pack-1" in str(EvidencePackNotFoundError("pack-1"))
