"""Cost showback and budget-period models (M49).

A ``CostEvent`` is an append-only, fully attributed usage fact. Budget periods
are day/week/month buckets per scope with a carry rule and threshold alerts.
``ShowbackRow``/``ShowbackReport`` are the read surface for cost-per-completed-task.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class CostError(Exception):
    """Base class for cost/showback failures."""


class UnattributedUsageError(CostError):
    """Raised when a usage event is missing mandatory attribution tags."""

    def __init__(self, event_id: str, missing: list[str]) -> None:
        super().__init__(f"usage event {event_id!r} missing attribution: {missing}")
        self.event_id = event_id
        self.missing = missing


class SpendCapExceededError(CostError):
    """Raised when a tenant period cap would be exceeded (fail-closed)."""

    def __init__(self, tenant_id: str, cap_usd: float, spent_usd: float) -> None:
        super().__init__(
            f"tenant {tenant_id!r} spend cap {cap_usd} would be exceeded (spent {spent_usd})"
        )
        self.tenant_id = tenant_id
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd


class CarryRule(StrEnum):
    """How period budget rolls over to the next period."""

    NONE = "none"
    CAPPED = "capped"
    FULL = "full"


class BudgetScope(StrEnum):
    """What a budget period covers."""

    TENANT = "tenant"
    TEAM = "team"
    WORKLOAD = "workload"


class CostEvent(BaseModel):
    """An append-only attributed usage fact (tenant → team → workload)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, max_length=64)
    team_id: str = Field(min_length=1, max_length=64)
    workload_id: str = Field(default="", max_length=253)
    run_id: str | None = Field(default=None, max_length=64)
    pipeline_run_id: str | None = Field(default=None, max_length=64)
    cost_type: CostType = CostType.LLM
    model: str | None = Field(default=None, max_length=253)
    cost_usd: float = Field(ge=0.0)
    saved_usd: float = Field(default=0.0, ge=0.0)
    completed: bool = False
    retry: bool = False
    escalation: bool = False
    occurred_at: AwareDatetime


class BudgetPeriod(BaseModel):
    """A budget bucket for one scope and period."""

    model_config = ConfigDict(extra="forbid")

    period_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    scope: BudgetScope
    scope_id: str = Field(min_length=1, max_length=253)
    kind: CostPeriodKind
    period_key: str = Field(min_length=1, max_length=32)
    limit_usd: float = Field(ge=0.0)
    carry_rule: CarryRule = CarryRule.NONE
    carry_in_usd: float = Field(default=0.0, ge=0.0)
    spent_usd: float = Field(default=0.0, ge=0.0)
    cap_usd: float | None = Field(default=None, ge=0.0)
    enforced: bool = False

    @property
    def effective_limit_usd(self) -> float:
        """Return the limit including carry-in."""
        return self.limit_usd + self.carry_in_usd

    @property
    def remaining_usd(self) -> float:
        """Return remaining budget (never negative)."""
        return max(0.0, self.effective_limit_usd - self.spent_usd)


class ThresholdAlert(BaseModel):
    """A fired budget-threshold crossing (once per period+threshold)."""

    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    scope: BudgetScope
    scope_id: str = Field(min_length=1, max_length=253)
    kind: CostPeriodKind
    period_key: str = Field(min_length=1)
    threshold: int = Field(ge=1)
    spent_usd: float = Field(ge=0.0)
    limit_usd: float = Field(ge=0.0)
    fired_at: AwareDatetime


class ShowbackRow(BaseModel):
    """Spend and outcome for one attribution group (team or workload)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str | None = None
    workload_id: str | None = None
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    wasted_usd: float = Field(default=0.0, ge=0.0)
    cache_savings_usd: float = Field(default=0.0, ge=0.0)
    completed_tasks: int = Field(default=0, ge=0)
    cost_per_completed_task: float = Field(default=0.0, ge=0.0)


class ShowbackReport(BaseModel):
    """The tenant showback view for one period."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    kind: CostPeriodKind
    period_key: str = Field(min_length=1)
    group_by: str = Field(default="team")
    rows: list[ShowbackRow] = Field(default_factory=list)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    total_completed_tasks: int = Field(default=0, ge=0)
    fleet_cpct: float = Field(default=0.0, ge=0.0)
    cache_savings_usd: float = Field(default=0.0, ge=0.0)
    unattributed: int = Field(default=0, ge=0)
