"""Cost showback API (M49-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hiveplane.api.deps import get_cost_service, get_tenant_context, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.cost.models import (
    BudgetPeriod,
    BudgetScope,
    CarryRule,
    ShowbackReport,
    ThresholdAlert,
)
from hiveplane.cost.service import CostService
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["cost"])

CostDep = Annotated[CostService, Depends(get_cost_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
CacheManager = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.REPORTS_MANAGE))
]
BudgetManager = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.REPORTS_MANAGE))
]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


def _scoped_tenant(tenant_id: str, ctx: TenantContext) -> str:
    """Reject a caller-supplied tenant that the acting context cannot see."""
    if not ctx.scopes(tenant_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant out of scope")
    return tenant_id


def _period(value: str) -> CostPeriodKind:
    try:
        return CostPeriodKind(value)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid period {value!r}"
        ) from exc


@router.get("/cost/showback/{tenant_id}/{team_id}", response_model=ShowbackReport)
def showback_by_team(
    tenant_id: str,
    team_id: str,
    service: CostDep,
    _: FleetReader,
    tenants: TenantDep,
    period: str = "month",
) -> ShowbackReport:
    """Return cost showback for a tenant/team period."""
    _scoped_tenant(tenant_id, tenants)
    return service.showback(tenant_id, _period(period), group_by="team", ctx=tenants)


@router.get("/cost/showback", response_model=ShowbackReport)
def showback(
    service: CostDep,
    _: FleetReader,
    tenants: TenantDep,
    period: str = "month",
    group_by: str = "team",
) -> ShowbackReport:
    """Return cost showback for the acting tenant, grouped by team or workload."""
    return service.showback(
        tenants.tenant_id, _period(period), group_by=group_by, ctx=tenants
    )


# --------------------------------------------------------------------------- #
# Cost depth (M50)
# --------------------------------------------------------------------------- #
from pydantic import BaseModel, ConfigDict  # noqa: E402

from hiveplane.api.deps import (  # noqa: E402
    get_cost_estimator,
    get_result_cache,
)
from hiveplane.cost.depth import (  # noqa: E402
    CacheEntry,
    CacheLookup,
    CostEstimate,
    CostEstimator,
    ResultCache,
)

EstimatorDep = Annotated[CostEstimator, Depends(get_cost_estimator)]
CacheDep = Annotated[ResultCache, Depends(get_result_cache)]


class CacheStoreRequest(BaseModel):
    """Request body to store an attested cache entry."""

    model_config = ConfigDict(extra="forbid")

    key: str
    workload_id: str
    manifest_version: int
    attestation_id: str
    result_ref: str
    saved_usd: float = 0.0


class CacheLookupRequest(BaseModel):
    """Request body to look up a cache entry."""

    model_config = ConfigDict(extra="forbid")

    key: str
    manifest_version: int
    attestation_id: str | None = None


class BudgetPeriodRequest(BaseModel):
    """Request body to create or update a budget period (M49-03)."""

    model_config = ConfigDict(extra="forbid")

    scope: BudgetScope
    scope_id: str
    kind: CostPeriodKind
    limit_usd: float
    carry_rule: CarryRule = CarryRule.NONE
    cap_usd: float | None = None
    enforced: bool = False


@router.post("/cost/budgets", response_model=BudgetPeriod)
def set_budget(
    request: BudgetPeriodRequest,
    service: CostDep,
    tenants: TenantDep,
    _: BudgetManager,
) -> BudgetPeriod:
    """Create or update the acting tenant's budget period (limit/cap/enforcement)."""
    return service.set_budget(
        tenants.tenant_id,
        request.scope,
        request.scope_id,
        request.kind,
        request.limit_usd,
        carry_rule=request.carry_rule,
        cap_usd=request.cap_usd,
        enforced=request.enforced,
        ctx=tenants,
    )


@router.get("/cost/periods", response_model=list[BudgetPeriod])
def list_budget_periods(
    service: CostDep, tenants: TenantDep, _: FleetReader
) -> list[BudgetPeriod]:
    """Return the acting tenant's budget periods (spend, limits, caps)."""
    return service.periods(tenants.tenant_id, ctx=tenants)


@router.get("/cost/alerts", response_model=list[ThresholdAlert])
def list_budget_alerts(
    service: CostDep, tenants: TenantDep, _: FleetReader
) -> list[ThresholdAlert]:
    """Return the acting tenant's fired budget-threshold alerts."""
    return service.alerts(tenants.tenant_id, ctx=tenants)


@router.get("/cost/estimates/{workload_id}", response_model=CostEstimate)
def cost_estimate(
    workload_id: str, estimator: EstimatorDep, _: FleetReader, task_type: str = "default"
) -> CostEstimate:
    """Return the pre-admission cost estimate for a workload/task type."""
    return estimator.estimate(workload_id, task_type)


@router.post("/cost/cache/lookup", response_model=CacheLookup)
def cache_lookup(
    request: CacheLookupRequest, cache: CacheDep, tenants: TenantDep, _: FleetReader
) -> CacheLookup:
    """Look up an attested cached result, accounting a hit as savings."""
    return cache.lookup(
        request.key,
        manifest_version=request.manifest_version,
        attestation_id=request.attestation_id,
        tenant_id=tenants.tenant_id,
    )


@router.post("/cost/cache/store", response_model=CacheEntry)
def cache_store(
    request: CacheStoreRequest, cache: CacheDep, tenants: TenantDep, _: CacheManager
) -> CacheEntry:
    """Store an attested cached result for the acting tenant."""
    return cache.store(
        key=request.key,
        workload_id=request.workload_id,
        manifest_version=request.manifest_version,
        attestation_id=request.attestation_id,
        result_ref=request.result_ref,
        saved_usd=request.saved_usd,
        tenant_id=tenants.tenant_id,
    )


@router.get("/cost/roi/fleet")
def cost_roi(
    service: CostDep,
    _: FleetReader,
    tenants: TenantDep,
    period: str = "month",
) -> dict[str, object]:
    """Return the fleet ROI report (spend vs outcome + flags)."""
    report = service.roi(tenants.tenant_id, _period(period), ctx=tenants)
    return report.model_dump(mode="json")


@router.get("/cost/forecast")
def cost_forecast(
    service: CostDep,
    _: FleetReader,
    tenants: TenantDep,
    period: str = "month",
) -> dict[str, object]:
    """Return the burn forecast and overrun prediction for a period."""
    return service.forecast(tenants.tenant_id, _period(period), ctx=tenants).model_dump(
        mode="json"
    )


@router.get("/cost/metering/export")
def cost_metering(
    service: CostDep,
    _: FleetReader,
    tenants: TenantDep,
) -> list[dict[str, object]]:
    """Export per-attribution usage for the acting tenant (chargeback)."""
    return [
        row.model_dump(mode="json")
        for row in service.chargeback(tenants.tenant_id, ctx=tenants)
    ]
