"""Registry API: workload CRUD, fleet catalog, versions, and dry-run (M3-M4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError

from hiveplane.api.deps import get_registry_service
from hiveplane.certification.models import Attestation, CertificationStatus
from hiveplane.core.manifest import parse_manifest
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.triggers import TriggerRule
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.models import (
    AdmissionContext,
    AdmissionDecision,
    CatalogEntry,
    EnforcementSummary,
    PromoteRequest,
    ToolRecord,
    ToolRegistration,
    TriggerRecord,
    VersionDiff,
    WorkloadRecord,
    WorkloadVersion,
)
from hiveplane.registry.service import RegistryService

router = APIRouter(tags=["registry"])

ServiceDep = Annotated[RegistryService, Depends(get_registry_service)]
ManifestBody = Annotated[dict[str, Any], Body()]


def _parse_manifest_or_422(payload: dict[str, Any]) -> AgentWorkload:
    try:
        return parse_manifest(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(include_url=False),
        ) from exc


def _to_catalog(record: WorkloadRecord) -> CatalogEntry:
    return CatalogEntry(
        name=record.name,
        owner=record.owner,
        team=record.team,
        runtime=record.runtime,
        certification_status=record.certification_status,
        current_version=record.current_version,
        updated_at=record.updated_at,
        last_run_at=record.last_run_at,
        failure_count=record.failure_count,
        last_failure_at=record.last_failure_at,
    )


@router.post("/workloads", response_model=WorkloadRecord | EnforcementSummary)
def register_workload(
    payload: ManifestBody,
    response: Response,
    service: ServiceDep,
    dry_run: bool = False,
) -> WorkloadRecord | EnforcementSummary:
    """Register a workload, or report enforcement without persisting (dry run)."""
    manifest = _parse_manifest_or_422(payload)
    result: WorkloadRecord | EnforcementSummary
    if dry_run:
        result = service.create(manifest, dry_run=True)
        response.status_code = status.HTTP_200_OK
    else:
        result = service.create(manifest, dry_run=False)
        response.status_code = status.HTTP_201_CREATED
    return result


@router.get("/workloads", response_model=list[CatalogEntry])
def list_workloads(
    service: ServiceDep,
    owner: str | None = None,
    team: str | None = None,
    runtime: RuntimeAdapter | None = None,
    certification_status: CertificationStatus | None = None,
    sort: str = "name",
    descending: bool = False,
    limit: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
) -> list[CatalogEntry]:
    """List the fleet catalog with filters, sorting, and pagination."""
    try:
        records = service.list_workloads(
            owner=owner,
            team=team,
            runtime=runtime,
            certification_status=certification_status,
            sort=sort,
            descending=descending,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return [_to_catalog(record) for record in records]


@router.get("/workloads/{name}", response_model=WorkloadRecord)
def get_workload(name: str, service: ServiceDep) -> WorkloadRecord:
    """Get the current registered state of a workload."""
    return service.get(name)


@router.put("/workloads/{name}", response_model=WorkloadRecord | EnforcementSummary)
def update_workload(
    name: str,
    payload: ManifestBody,
    response: Response,
    service: ServiceDep,
    dry_run: bool = False,
) -> WorkloadRecord | EnforcementSummary:
    """Register a new manifest version for a workload."""
    manifest = _parse_manifest_or_422(payload)
    if manifest.name != name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"manifest name {manifest.name!r} does not match path {name!r}",
        )
    result: WorkloadRecord | EnforcementSummary
    if dry_run:
        result = service.update(name, manifest, dry_run=True)
    else:
        result = service.update(name, manifest, dry_run=False)
    response.status_code = status.HTTP_200_OK
    return result


@router.delete("/workloads/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workload(name: str, service: ServiceDep) -> None:
    """Deregister a workload."""
    service.delete(name)


@router.get("/workloads/{name}/versions", response_model=list[WorkloadVersion])
def list_versions(name: str, service: ServiceDep) -> list[WorkloadVersion]:
    """Return the append-only manifest version history for a workload."""
    return service.versions(name)


@router.get("/workloads/{name}/versions/diff", response_model=VersionDiff)
def version_diff(
    name: str,
    service: ServiceDep,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
) -> VersionDiff:
    """Return the field-level diff between two manifest versions."""
    return service.version_diff(name, from_version, to_version)


@router.get("/workloads/{name}/admission", response_model=AdmissionDecision)
def check_admission(
    name: str,
    service: ServiceDep,
    context: AdmissionContext = AdmissionContext.PRODUCTION,
) -> AdmissionDecision:
    """Check whether a workload may be admitted to a target context."""
    return service.check_admission(name, context)


@router.post("/workloads/{name}/promote", response_model=WorkloadRecord)
def promote_workload(name: str, payload: PromoteRequest, service: ServiceDep) -> WorkloadRecord:
    """Promote a manifest version, blocked until re-certification passes."""
    return service.promote(name, payload.version)


@router.get("/workloads/{name}/attestations", response_model=list[Attestation])
def list_attestations(name: str, service: ServiceDep) -> list[Attestation]:
    """List verified attestations for a workload."""
    return service.list_attestations(name)


@router.get("/attestations/{attestation_id}", response_model=Attestation)
def get_attestation(attestation_id: str, service: ServiceDep) -> Attestation:
    """Return an attestation after verifying its signature."""
    return service.get_attestation(attestation_id)


@router.get("/tools", response_model=list[ToolRecord])
def list_tools(
    service: ServiceDep,
    trust_level: ToolTrustLevel | None = None,
    mcp_server: str | None = None,
) -> list[ToolRecord]:
    """List registered MCP tools."""
    return service.list_tools(trust_level=trust_level, mcp_server=mcp_server)


@router.post("/tools", response_model=ToolRecord, status_code=status.HTTP_201_CREATED)
def register_tool(payload: ToolRegistration, service: ServiceDep) -> ToolRecord:
    """Register an MCP tool definition."""
    return service.register_tool(
        ToolRecord(
            **payload.model_dump(),
            registered_at=datetime.now(UTC),
            registered_by="api",
        )
    )


@router.get("/workloads/{name}/triggers", response_model=list[TriggerRecord])
def list_triggers(name: str, service: ServiceDep) -> list[TriggerRecord]:
    """List trigger rules stored for a workload."""
    return service.list_triggers(name)


@router.post(
    "/workloads/{name}/triggers",
    response_model=TriggerRecord,
    status_code=status.HTTP_201_CREATED,
)
def add_trigger(name: str, payload: TriggerRule, service: ServiceDep) -> TriggerRecord:
    """Add a trigger rule to a workload."""
    return service.add_trigger(name, payload)


@router.delete(
    "/workloads/{name}/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_trigger(name: str, trigger_id: str, service: ServiceDep) -> None:
    """Remove a trigger rule from a workload."""
    service.delete_trigger(name, trigger_id)
