"""Reporting reads API: generated report runs (M57)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_audit_export_service,
    get_delivery_service,
    get_digest_scheduler,
    get_digest_service,
    get_evidence_service,
    get_pii_scrubber,
    get_reporting_store,
    get_retention_enforcer,
    get_tenant_context,
    get_tenant_purge_service,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.delivery.models import (
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryEnvelope,
    DeliveryEventType,
)
from hiveplane.delivery.service import DeliveryService
from hiveplane.fleet.artifacts import RetentionPolicy
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.reporting.audit_export import AuditExportService
from hiveplane.reporting.digest import DigestService
from hiveplane.reporting.errors import (
    EvidencePackNotFoundError,
    PurgeIncompleteError,
    ReportingError,
)
from hiveplane.reporting.evidence import EvidencePackService
from hiveplane.reporting.models import (
    AuditExport,
    AuditExportFormat,
    DigestContent,
    EvidencePack,
    PurgeRecord,
    ReportKind,
    ReportRun,
    ReportSchedule,
    RetentionDataClass,
    RetentionPurgeResult,
    ScrubResult,
)
from hiveplane.reporting.pii import PIIScrubber
from hiveplane.reporting.purge import TenantPurgeService
from hiveplane.reporting.retention import RetentionEnforcer
from hiveplane.reporting.scheduler import DigestScheduler
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.cron import CronError

router = APIRouter(tags=["reporting"])

StoreDep = Annotated[ReportingStore, Depends(get_reporting_store)]
DigestDep = Annotated[DigestService, Depends(get_digest_service)]
DeliveryDep = Annotated[DeliveryService, Depends(get_delivery_service)]
SchedulerDep = Annotated[DigestScheduler, Depends(get_digest_scheduler)]
AuditExportDep = Annotated[AuditExportService, Depends(get_audit_export_service)]
EvidenceDep = Annotated[EvidencePackService, Depends(get_evidence_service)]
EnforcerDep = Annotated[RetentionEnforcer, Depends(get_retention_enforcer)]
PurgeDep = Annotated[TenantPurgeService, Depends(get_tenant_purge_service)]
ScrubberDep = Annotated[PIIScrubber, Depends(get_pii_scrubber)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
ReportsManager = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.REPORTS_MANAGE))
]


class DigestSendRequest(BaseModel):
    """Request body to generate and deliver a digest immediately."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    team_id: str | None = None
    period: CostPeriodKind = CostPeriodKind.WEEK


class ScheduleCreateRequest(BaseModel):
    """Request body to create a per-team digest schedule."""

    model_config = ConfigDict(extra="forbid")

    team_id: str | None = None
    cron: str = Field(min_length=1, max_length=128)
    period_kind: CostPeriodKind = CostPeriodKind.WEEK
    channels: list[DeliveryChannel] = Field(default_factory=list)


class EvidencePackRequest(BaseModel):
    """Request body to assemble a signed compliance evidence pack."""

    model_config = ConfigDict(extra="forbid")

    period_start: AwareDatetime
    period_end: AwareDatetime


class RetentionPolicyRequest(BaseModel):
    """Request body to set a per-tenant retention policy for one data class."""

    model_config = ConfigDict(extra="forbid")

    retain_days: int = Field(ge=0)
    legal_hold: bool = False


class ScrubRequest(BaseModel):
    """Request body to redact PII from a text value."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None


@router.post("/pii/scrub", response_model=ScrubResult)
def scrub_pii(
    payload: ScrubRequest, scrubber: ScrubberDep, identity: ReportsManager
) -> ScrubResult:
    """Detect and redact PII in the supplied text; nothing is persisted."""
    return scrubber.scrub(payload.text)


@router.get("/retention/policies", response_model=list[RetentionPolicy])
def list_retention_policies(
    enforcer: EnforcerDep, ctx: TenantDep, identity: FleetReader
) -> list[RetentionPolicy]:
    """List the acting tenant's per-class retention policies."""
    return enforcer.policies(ctx.tenant_id)


@router.put("/retention/policies/{data_class}", response_model=RetentionPolicy)
def put_retention_policy(
    data_class: RetentionDataClass,
    payload: RetentionPolicyRequest,
    enforcer: EnforcerDep,
    ctx: TenantDep,
    identity: ReportsManager,
) -> RetentionPolicy:
    """Create or replace the acting tenant's retention policy for a data class."""
    return enforcer.set_policy(
        ctx.tenant_id,
        data_class,
        payload.retain_days,
        payload.legal_hold,
        actor=identity.operator_id,
    )


@router.post("/retention/enforce", response_model=RetentionPurgeResult)
def enforce_retention(
    enforcer: EnforcerDep, ctx: TenantDep, identity: ReportsManager
) -> RetentionPurgeResult:
    """Run the acting tenant's due retention purge across every store."""
    return enforcer.purge_due(tenant_id=ctx.tenant_id)



@router.get("/reports", response_model=list[ReportRun])
def list_reports(
    store: StoreDep,
    ctx: TenantDep,
    identity: FleetReader,
    kind: ReportKind | None = None,
) -> list[ReportRun]:
    """List the acting tenant's generated report runs."""
    return store.list_reports(tenant_id=ctx.tenant_id, kind=kind, ctx=ctx)


@router.get("/reports/digest", response_model=None)
def fleet_digest(
    service: DigestDep,
    ctx: TenantDep,
    identity: FleetReader,
    period: CostPeriodKind = CostPeriodKind.WEEK,
    output_format: Annotated[Literal["json", "markdown"], Query(alias="format")] = "json",
) -> DigestContent | PlainTextResponse:
    """Render the acting tenant's fleet digest as JSON or markdown."""
    content = service.build(ctx, kind=period)
    if output_format == "markdown":
        return PlainTextResponse(service.render_markdown(content), media_type="text/markdown")
    return content


@router.post("/reports/digest/send", response_model=list[DeliveryAttempt])
def send_digest(
    payload: DigestSendRequest,
    digest: DigestDep,
    delivery: DeliveryDep,
    ctx: TenantDep,
    identity: ReportsManager,
) -> list[DeliveryAttempt]:
    """Generate the acting tenant's digest and deliver it immediately."""
    tenant_id = payload.tenant_id or ctx.tenant_id
    if not ctx.scopes(tenant_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant out of scope")
    content, _ = digest.generate(tenant_id, kind=payload.period)
    envelope = DeliveryEnvelope(
        event_type=DeliveryEventType.COMPLETED,
        tenant_id=tenant_id,
        team_id=payload.team_id,
        summary=digest.render_markdown(content),
    )
    preference = delivery.preference(payload.team_id, tenant_id, ctx=ctx)
    if preference is not None and preference.tenant_id == tenant_id:
        destinations = list(preference.destinations)
    else:
        destinations = []
    return delivery.deliver(envelope, destinations)


@router.post("/reports/schedules", response_model=ReportSchedule)
def create_schedule(
    payload: ScheduleCreateRequest,
    scheduler: SchedulerDep,
    ctx: TenantDep,
    identity: ReportsManager,
) -> ReportSchedule:
    """Create or replace a per-team digest schedule for the acting tenant."""
    try:
        return scheduler.schedule(
            ctx.tenant_id,
            team_id=payload.team_id,
            cron=payload.cron,
            period_kind=payload.period_kind,
            channels=payload.channels,
            ctx=ctx,
        )
    except CronError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/reports/schedules", response_model=list[ReportSchedule])
def list_schedules(
    store: StoreDep, ctx: TenantDep, identity: FleetReader
) -> list[ReportSchedule]:
    """List the acting tenant's digest schedules."""
    return store.list_schedules(ctx=ctx)


@router.post("/reports/schedules/run-due", response_model=list[DeliveryAttempt])
def run_due_schedules(
    scheduler: SchedulerDep, ctx: TenantDep, identity: ReportsManager
) -> list[DeliveryAttempt]:
    """Run every matured digest schedule in the acting tenant (all when system)."""
    return scheduler.run_all_due(ctx=ctx)


@router.get("/reports/{report_id}", response_model=ReportRun)
def get_report(
    report_id: str, store: StoreDep, ctx: TenantDep, identity: FleetReader
) -> ReportRun:
    """Return one generated report run in the acting tenant."""
    report = store.get_report(report_id, tenant_id=ctx.tenant_id, ctx=ctx)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return report


@router.get("/audit/export", response_model=AuditExport)
def export_audit_log(
    service: AuditExportDep,
    ctx: TenantDep,
    identity: ReportsManager,
    period_start: Annotated[AwareDatetime, Query()],
    period_end: Annotated[AwareDatetime, Query()],
    export_format: Annotated[AuditExportFormat, Query(alias="format")] = AuditExportFormat.JSON,
) -> AuditExport:
    """Export the acting tenant's audit range with an integrity proof."""
    try:
        return service.export(
            ctx,
            period_start=period_start,
            period_end=period_end,
            format=export_format,
        )
    except ReportingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/audit/exports", response_model=list[AuditExport])
def list_audit_exports(
    service: AuditExportDep, ctx: TenantDep, identity: FleetReader
) -> list[AuditExport]:
    """List the acting tenant's persisted audit exports."""
    return service.list(ctx)


@router.get("/audit/exports/{export_id}", response_model=AuditExport)
def get_audit_export(
    export_id: str, store: StoreDep, ctx: TenantDep, identity: FleetReader
) -> AuditExport:
    """Return one audit export in the acting tenant."""
    export = store.get_export(export_id, tenant_id=ctx.tenant_id, ctx=ctx)
    if export is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audit export not found")
    return export


@router.post("/compliance/evidence-pack", response_model=EvidencePack)
def create_evidence_pack(
    payload: EvidencePackRequest,
    service: EvidenceDep,
    ctx: TenantDep,
    identity: ReportsManager,
) -> EvidencePack:
    """Assemble, sign, persist, and audit an evidence pack for the period."""
    return service.generate(
        ctx, period_start=payload.period_start, period_end=payload.period_end
    )


@router.get("/compliance/evidence-key", response_model=None)
def get_evidence_key(
    service: EvidenceDep, ctx: TenantDep, identity: FleetReader
) -> PlainTextResponse:
    """Return the PEM public key an auditor uses to verify evidence packs."""
    return PlainTextResponse(service.public_key_pem(), media_type="application/x-pem-file")


@router.get("/compliance/evidence-packs", response_model=list[EvidencePack])
def list_evidence_packs(
    service: EvidenceDep, ctx: TenantDep, identity: FleetReader
) -> list[EvidencePack]:
    """List the acting tenant's persisted evidence packs."""
    return service.list(ctx)


@router.get("/compliance/evidence-packs/{pack_id}", response_model=EvidencePack)
def get_evidence_pack(
    pack_id: str, service: EvidenceDep, ctx: TenantDep, identity: FleetReader
) -> EvidencePack:
    """Return one evidence pack in the acting tenant."""
    try:
        return service.get(pack_id, ctx=ctx)
    except EvidencePackNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/tenants/{tenant_id}/purge", response_model=PurgeRecord)
def purge_tenant(
    tenant_id: str,
    service: PurgeDep,
    ctx: TenantDep,
    identity: ReportsManager,
) -> PurgeRecord:
    """Purge every store's data for a tenant and issue a signed certificate."""
    if not ctx.scopes(tenant_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant out of scope")
    try:
        return service.purge(ctx, tenant_id, actor=identity.operator_id)
    except PurgeIncompleteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/tenants/{tenant_id}/purge-records", response_model=list[PurgeRecord])
def list_purge_records(
    tenant_id: str,
    service: PurgeDep,
    ctx: TenantDep,
    identity: FleetReader,
) -> list[PurgeRecord]:
    """List a tenant's persisted purge records."""
    if not ctx.scopes(tenant_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant out of scope")
    return [record for record in service.list(ctx) if record.tenant_id == tenant_id]
