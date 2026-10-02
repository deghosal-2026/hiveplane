"""Artifact storage, retention, and portability API (M54, D38)."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_artifact_service,
    get_retention_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.artifacts.export import (
    export_fleet_bundle,
    import_fleet_bundle,
    plan_import,
)
from hiveplane.artifacts.models import (
    PurgeResult,
    RetentionPolicyConflictError,
    RetentionPolicyNotFoundError,
)
from hiveplane.artifacts.service import (
    ArtifactNotFoundError,
    ArtifactService,
    RetentionService,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.fleet.artifacts import Artifact, RetentionPolicy
from hiveplane.tenancy.context import TenantContext
from hiveplane.transparency.errors import BundleVerificationError

router = APIRouter(tags=["artifacts"])

ArtifactDep = Annotated[ArtifactService, Depends(get_artifact_service)]
RetentionDep = Annotated[RetentionService, Depends(get_retention_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
ArtifactAdmin = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.ARTIFACTS_MANAGE))
]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class CaptureRequest(BaseModel):
    """Request body to store a run artifact (base64-encoded bytes)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    retention_policy_id: str | None = Field(default=None, max_length=64)


class RetentionPolicyRequest(BaseModel):
    """Request body to create or update a retention policy."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=64)
    data_class: str = Field(min_length=1, max_length=128)
    retain_days: int = Field(ge=0)
    legal_hold: bool = False


class BundleRequest(BaseModel):
    """Request body carrying a portable fleet bundle."""

    model_config = ConfigDict(extra="forbid")

    workload: dict[str, Any] | None = None
    corpus: dict[str, Any] | None = None
    policy_pack: dict[str, Any] | None = None


class ImportRequest(BaseModel):
    """Request body carrying a bundle to import."""

    model_config = ConfigDict(extra="forbid")

    bundle: dict[str, Any]
    allow_unsigned: bool = False


@router.post("/artifacts", response_model=Artifact)
def capture_artifact(
    request: CaptureRequest,
    service: ArtifactDep,
    identity: ArtifactAdmin,
    tenants: TenantDep,
) -> Artifact:
    """Store an artifact content-addressed and link it to a run."""
    try:
        data = base64.b64decode(request.content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "content must be base64") from exc
    try:
        return service.capture(
            tenant_id=tenants.tenant_id,
            run_id=request.run_id,
            filename=request.filename,
            data=data,
            retention_policy_id=request.retention_policy_id,
            actor=identity.operator_id,
            ctx=tenants,
        )
    except RetentionPolicyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/artifacts", response_model=list[Artifact])
def list_artifacts(
    service: ArtifactDep,
    identity: FleetReader,
    tenants: TenantDep,
    run_id: str | None = None,
) -> list[Artifact]:
    """List artifacts in the tenant, optionally for one run."""
    return service.list(tenant_id=tenants.tenant_id, run_id=run_id, ctx=tenants)


@router.get("/artifacts/{artifact_id}", response_model=Artifact)
def get_artifact(
    artifact_id: str, service: ArtifactDep, identity: FleetReader, tenants: TenantDep
) -> Artifact:
    """Return artifact metadata."""
    try:
        return service.get(artifact_id, tenant_id=tenants.tenant_id, ctx=tenants)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: str, service: ArtifactDep, identity: FleetReader, tenants: TenantDep
) -> Response:
    """Return an artifact's bytes."""
    try:
        _, data = service.data(artifact_id, tenant_id=tenants.tenant_id, ctx=tenants)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(content=data, media_type="application/octet-stream")


@router.post("/retention/policies", response_model=RetentionPolicy)
def set_retention_policy(
    request: RetentionPolicyRequest,
    service: RetentionDep,
    identity: ArtifactAdmin,
    tenants: TenantDep,
) -> RetentionPolicy:
    """Create or update a tenant retention policy."""
    try:
        return service.set_policy(
            RetentionPolicy(
                policy_id=request.policy_id,
                tenant_id=tenants.tenant_id,
                data_class=request.data_class,
                retain_days=request.retain_days,
                legal_hold=request.legal_hold,
            ),
            actor=identity.operator_id,
            ctx=tenants,
        )
    except RetentionPolicyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/retention/policies", response_model=list[RetentionPolicy])
def list_retention_policies(
    service: RetentionDep, identity: FleetReader, tenants: TenantDep
) -> list[RetentionPolicy]:
    """List a tenant's retention policies."""
    return service.policies(tenants.tenant_id, ctx=tenants)


@router.post("/retention/purge", response_model=PurgeResult)
def purge_expired(
    service: RetentionDep, identity: ArtifactAdmin, tenants: TenantDep
) -> PurgeResult:
    """Purge expired artifacts, leaving audit evidence (legal holds skipped)."""
    return service.purge_due(tenant_id=tenants.tenant_id, ctx=tenants)


@router.post("/export")
def export_bundle(
    request: Request,
    body: BundleRequest,
    identity: ArtifactAdmin,
    ctx: TenantDep,
) -> dict[str, Any]:
    """Export a signed manifest + corpus + policy-pack bundle.

    Bundles are plane-level artifacts with no tenant dimension, so the export is
    an explicit system/admin operation; the acting tenant is carried only for the
    ``ARTIFACTS_MANAGE`` authorization decision above.
    """
    private_key = getattr(request.app.state, "artifact_signing_key", None)
    key_id = getattr(request.app.state, "artifact_signing_key_id", "control-plane")
    bundle = export_fleet_bundle(
        workload=body.workload,
        corpus=body.corpus,
        policy_pack=body.policy_pack,
        private_key=private_key,
        key_id=key_id,
    )
    return bundle.model_dump(mode="json")


@router.post("/import")
def import_bundle(
    request: Request,
    body: ImportRequest,
    identity: ArtifactAdmin,
    ctx: TenantDep,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Verify a bundle and return the import plan; never auto-executes workloads."""
    public_key = getattr(request.app.state, "artifact_verification_key", None)
    try:
        bundle = import_fleet_bundle(
            body.bundle, public_key, allow_unsigned=body.allow_unsigned
        )
    except BundleVerificationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    plan = plan_import(bundle)
    return plan.model_dump(mode="json") | {"dry_run": dry_run}
