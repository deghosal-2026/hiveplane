"""Live-state readers backing the `ask` copilot with real services (M53)."""

from __future__ import annotations

from pydantic import JsonValue

from hiveplane.cost.service import CostService
from hiveplane.execution.service import RunService
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.health.service import HealthService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.tenancy.context import TenantContext
from hiveplane.tenancy.models import Role


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, role=Role.ADMIN)


class ServiceReaders:
    """An :class:`~hiveplane.ask.readers.AskReaders` over the control-plane services.

    Strictly read-only and tenant-scoped: it only calls query methods, and every
    call is bounded to the requesting tenant.
    """

    def __init__(
        self,
        *,
        run_service: RunService,
        cost_service: CostService,
        approval_service: ApprovalService,
        health_service: HealthService,
    ) -> None:
        self._runs = run_service
        self._cost = cost_service
        self._approvals = approval_service
        self._health = health_service

    def run(self, run_id: str, *, tenant_id: str) -> dict[str, JsonValue]:
        """Return the state and failure reason of a run in the tenant."""
        run = self._runs.get(run_id, ctx=_context(tenant_id))
        return {
            "id": run.id,
            "state": run.state.value,
            "failure_reason": run.failure_reason,
        }

    def team_spend(
        self, team: str, period: str, *, tenant_id: str
    ) -> dict[str, JsonValue]:
        """Return a team's attributed spend for a period."""
        report = self._cost.showback(
            tenant_id, CostPeriodKind(period), group_by="team"
        )
        rows = [row for row in report.rows if row.team_id == team]
        total = sum(row.total_cost_usd for row in rows) if team else report.total_cost_usd
        return {
            "team_id": team,
            "period": period,
            "total_cost_usd": total,
        }

    def approval(self, approval_id: str, *, tenant_id: str) -> dict[str, JsonValue]:
        """Return who decided an approval, and why, in the tenant."""
        record = self._approvals.get(approval_id, ctx=_context(tenant_id))
        return {
            "approval_id": record.approval_id,
            "status": record.status.value,
            "operator": record.decided_by,
            "reason": record.decision_reason,
        }

    def health(self, workload: str, *, tenant_id: str) -> dict[str, JsonValue]:
        """Return a workload's health status in the tenant."""
        health = self._health.workload(workload)
        return {
            "workload": health.workload,
            "status": health.status.value,
            "failure_rate": health.failure_rate,
        }

    def fleet(self, *, tenant_id: str) -> dict[str, JsonValue]:
        """Return what is currently running and paused in the tenant."""
        from hiveplane.core.run import RunState

        ctx = _context(tenant_id)
        running = self._runs.list_runs(state=RunState.RUNNING, ctx=ctx)
        paused = self._runs.list_runs(state=RunState.PAUSED, ctx=ctx)
        return {
            "running": [run.id for run in running],
            "paused": [run.id for run in paused],
        }
