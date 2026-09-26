"""Promotion gate API (M32-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_promotion_gate, get_tenant_context
from hiveplane.certification.models import TargetContext
from hiveplane.certification.promotion import PromotionGate, PromotionRecord
from hiveplane.registry.errors import VersionNotFoundError, WorkloadNotFoundError
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["promotions"])

GateDep = Annotated[PromotionGate, Depends(get_promotion_gate)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class PromotionRequest(BaseModel):
    """A request to promote a manifest version to a target context."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    to_context: TargetContext = TargetContext.PRODUCTION
    operator: str = Field(default="operator", min_length=1)


class RecertifyRequest(BaseModel):
    """A request to re-certify the current artifact, then promote it."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    corpus: str | None = None
    model_identity: str | None = None
    operator: str = Field(default="operator", min_length=1)


class RecertifyResponse(BaseModel):
    """The certification and promotion outcomes of a re-certify request."""

    model_config = ConfigDict(extra="forbid")

    promotion: PromotionRecord
    certification_id: str


@router.post("/promotions", response_model=PromotionRecord)
def request_promotion(
    request: PromotionRequest, gate: GateDep, ctx: TenantDep
) -> PromotionRecord:
    """Request promotion; returns 409 with the changed bindings when refused."""
    try:
        record = gate.promote(
            request.workload,
            request.manifest_version,
            to_context=request.to_context,
            operator=request.operator,
            ctx=ctx,
        )
    except (WorkloadNotFoundError, VersionNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not record.promoted:
        raise HTTPException(status.HTTP_409_CONFLICT, _refusal_detail(record))
    return record


@router.post("/promotions/recertify", response_model=RecertifyResponse)
def recertify_and_promote(
    request: RecertifyRequest, gate: GateDep, ctx: TenantDep
) -> RecertifyResponse:
    """Re-certify the current artifact and promote it, or return the refusal."""
    certification, promotion = gate.recertify_and_promote(
        request.workload,
        corpus_ref=request.corpus,
        model_identity=request.model_identity,
        operator=request.operator,
        ctx=ctx,
    )
    return RecertifyResponse(
        promotion=promotion, certification_id=certification.record_id
    )


@router.get("/promotions", response_model=list[PromotionRecord])
def list_promotions(gate: GateDep, ctx: TenantDep) -> list[PromotionRecord]:
    """List recorded promotion attempts (most recent first)."""
    records = gate.list_promotions(ctx=ctx)
    records.reverse()
    return records


@router.get("/promotions/{promotion_id}", response_model=PromotionRecord)
def get_promotion(
    promotion_id: str, gate: GateDep, ctx: TenantDep
) -> PromotionRecord:
    """Return one recorded promotion attempt."""
    try:
        return gate.get(promotion_id, ctx=ctx)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"promotion {promotion_id!r} not found"
        ) from exc


def _refusal_detail(record: PromotionRecord) -> str:
    changed = ", ".join(record.changed_bindings) or "none"
    return f"{record.refusal_reason} (changed bindings: {changed})"
