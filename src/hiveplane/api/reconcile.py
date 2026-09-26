"""Reconcile API: status, history, drift, plan, and apply (M26-04)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_reconcile_controller,
    get_reconcile_store,
    get_tenant_context,
)
from hiveplane.fleet.reconcile import DriftRecord
from hiveplane.reconcile.controller import ReconcileController
from hiveplane.reconcile.loader import GitSource
from hiveplane.reconcile.models import (
    ReconcileMode,
    ReconcileRun,
    ReconcileStatusView,
)
from hiveplane.reconcile.source import SourceKind, SourceRef
from hiveplane.reconcile.store import ReconcileStore
from hiveplane.tenancy import TenantContext

router = APIRouter(prefix="/reconcile", tags=["reconcile"])

ControllerDep = Annotated[ReconcileController, Depends(get_reconcile_controller)]
StoreDep = Annotated[ReconcileStore, Depends(get_reconcile_store)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class ReconcileRequest(BaseModel):
    """A request to reconcile a declared source."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    path: str | None = Field(default=None, max_length=2000)
    git: GitSource | None = None
    confirmed: bool = False

    def to_source(self, source_id: str) -> SourceRef:
        """Build the source reference for this request."""
        return SourceRef(
            source_id=source_id, kind=self.kind, path=self.path, git=self.git
        )


@router.get("/{source_id}", response_model=ReconcileStatusView)
def reconcile_status(
    source_id: str, controller: ControllerDep, ctx: TenantDep
) -> ReconcileStatusView:
    """Return a source's last revision, reconcile time, and open drift count."""
    return controller.status(source_id, tenant_id=ctx.tenant_id)


@router.get("/{source_id}/runs", response_model=list[ReconcileRun])
def reconcile_runs(
    source_id: str, store: StoreDep, ctx: TenantDep, limit: int = 20
) -> list[ReconcileRun]:
    """Return a source's reconcile-run history, oldest first."""
    return store.list_runs(source_id, limit=limit, ctx=ctx)


@router.get("/{source_id}/drift", response_model=list[DriftRecord])
def reconcile_drift(source_id: str, store: StoreDep, ctx: TenantDep) -> list[DriftRecord]:
    """Return the drift records observed while reconciling."""
    return store.list_drift(ctx=ctx)


@router.post("/{source_id}/plan", response_model=ReconcileRun)
def reconcile_plan(
    source_id: str, request: ReconcileRequest, controller: ControllerDep, ctx: TenantDep
) -> ReconcileRun:
    """Dry-run a reconcile: report the plan without mutating state."""
    return controller.reconcile(
        request.to_source(source_id),
        mode=ReconcileMode.PLAN,
        confirmed=request.confirmed,
        tenant_id=ctx.tenant_id,
    )


@router.post("/{source_id}/apply", response_model=ReconcileRun)
def reconcile_apply(
    source_id: str, request: ReconcileRequest, controller: ControllerDep, ctx: TenantDep
) -> ReconcileRun:
    """Apply a reconcile, gated by the configured guardrails."""
    return controller.reconcile(
        request.to_source(source_id),
        mode=ReconcileMode.APPLY,
        confirmed=request.confirmed,
        tenant_id=ctx.tenant_id,
    )
