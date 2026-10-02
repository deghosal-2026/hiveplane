"""Corpus publishing and versioning API (M55-03)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import get_corpus_service, get_tenant_context, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.certification.corpus import parse_corpus
from hiveplane.certification.errors import CorpusError
from hiveplane.corpus.models import CorpusRelease
from hiveplane.corpus.service import CorpusImmutabilityError, CorpusService
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["corpora"])

CorpusDep = Annotated[CorpusService, Depends(get_corpus_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
CorpusAdmin = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.CORPUS_MANAGE))
]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class PublishCorpusRequest(BaseModel):
    """Request body carrying a corpus document to publish."""

    model_config = ConfigDict(extra="forbid")

    corpus: dict[str, Any]


@router.post("/corpora", response_model=CorpusRelease)
def publish_corpus(
    request: PublishCorpusRequest,
    service: CorpusDep,
    identity: CorpusAdmin,
    tenants: TenantDep,
) -> CorpusRelease:
    """Validate and publish an immutable corpus version."""
    try:
        corpus = parse_corpus(request.corpus)
    except CorpusError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc
    try:
        return service.publish(corpus, tenant_id=tenants.tenant_id, ctx=tenants)
    except CorpusImmutabilityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/corpora", response_model=list[CorpusRelease])
def list_corpora(
    service: CorpusDep, identity: FleetReader, tenants: TenantDep
) -> list[CorpusRelease]:
    """List published corpus versions in the tenant."""
    return service.releases(tenant_id=tenants.tenant_id, ctx=tenants)


@router.get("/corpora/{corpus_id}/{version}", response_model=CorpusRelease)
def get_corpus(
    corpus_id: str,
    version: int,
    service: CorpusDep,
    identity: FleetReader,
    tenants: TenantDep,
) -> CorpusRelease:
    """Return a published corpus version."""
    release = next(
        (
            candidate
            for candidate in service.releases(tenant_id=tenants.tenant_id, ctx=tenants)
            if candidate.corpus_id == corpus_id and candidate.version == version
        ),
        None,
    )
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "corpus release not found")
    return release
