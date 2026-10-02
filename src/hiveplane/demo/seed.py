"""Demo profile seeding: a screenshot-ready fleet (M59-07)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.manifest import parse_manifest
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.models import CostEvent
from hiveplane.cost.service import CostService
from hiveplane.execution.store import RunStore
from hiveplane.fleet.cost import CostType
from hiveplane.registry.models import WorkloadRecord
from hiveplane.registry.store import RegistryStore
from hiveplane.tenancy import Role
from hiveplane.tenancy.admin import TenantAdminService
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantAlreadyExistsError

_TENANT_WORKLOADS: dict[str, tuple[tuple[str, str, CertificationStatus], ...]] = {
    "acme": (
        ("triage-agent", "support", CertificationStatus.CERTIFIED),
        ("refund-agent", "support", CertificationStatus.CERTIFIED),
        ("reconcile-agent", "finance", CertificationStatus.CERTIFIED),
    ),
    "beta": (
        ("forecast-agent", "finance", CertificationStatus.CERTIFIED),
        ("billing-agent", "finance", CertificationStatus.CERTIFIED),
        ("ops-agent", "platform", CertificationStatus.UNCERTIFIED),
    ),
}
_RUN_STATES = (RunState.COMPLETED, RunState.COMPLETED, RunState.FAILED, RunState.RUNNING)


class DemoSeedReport(BaseModel):
    """A summary of what the demo profile seeded."""

    model_config = ConfigDict(extra="forbid")

    profile: str
    tenant_ids: list[str]
    workloads: int
    runs: int
    metering_events: int
    seeded_at: AwareDatetime


def _manifest(name: str, team: str) -> AgentWorkload:
    return parse_manifest(
        {
            "apiVersion": "hiveplane/v1",
            "kind": "AgentWorkload",
            "metadata": {"name": name, "owner": f"{team}-team", "team": team},
            "spec": {
                "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
                "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
                "model": {
                    "strategy": "tiered",
                    "identity": {
                        "provider": "openai",
                        "family": "gpt-4o",
                        "version": "2024-08-06",
                    },
                },
                "certification": {
                    "benchmark_corpus": "corpora/demo/v1",
                    "staging_threshold": 0.8,
                    "production_threshold": 0.9,
                    "status": "uncertified",
                },
            },
        }
    )


class DemoSeeder:
    """Seeds a deterministic, idempotent demo fleet for screenshots and talks."""

    def __init__(
        self,
        tenant_admin: TenantAdminService,
        registry_store: RegistryStore,
        run_store: RunStore,
        cost_service: CostService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tenant_admin = tenant_admin
        self._registry_store = registry_store
        self._run_store = run_store
        self._cost_service = cost_service
        self._clock = clock or (lambda: datetime.now(UTC))

    def seed(self, *, profile: str = "default") -> DemoSeedReport:
        """Seed two tenants with certified workloads, runs, and metering."""
        now = self._clock()
        tenants = (("acme", "Acme Corp"), ("beta", "Beta Labs"))
        workloads = 0
        runs = 0
        events = 0
        for tenant_id, name in tenants:
            ctx = TenantContext(tenant_id=tenant_id, role=Role.ADMIN)
            with contextlib.suppress(TenantAlreadyExistsError):
                self._tenant_admin.create_tenant(
                    SYSTEM_CONTEXT, tenant_id=tenant_id, name=name
                )
            for workload_name, team, status in _TENANT_WORKLOADS[tenant_id]:
                manifest = _manifest(workload_name, team)
                self._registry_store.save_workload(
                    WorkloadRecord(
                        name=workload_name,
                        manifest=manifest,
                        current_version=1,
                        certification_status=status,
                        owner=manifest.owner,
                        team=team,
                        runtime=manifest.spec.runtime.adapter,
                        created_at=now,
                        updated_at=now,
                        tenant_id=tenant_id,
                    ),
                    ctx=ctx,
                )
                workloads += 1
            for index, state in enumerate(_RUN_STATES):
                tenant_workloads = _TENANT_WORKLOADS[tenant_id]
                workload_name = tenant_workloads[index % len(tenant_workloads)][0]
                self._run_store.save_run(
                    Run(
                        id=f"demo-{tenant_id}-{index}",
                        workload_id=workload_name,
                        caller="demo",
                        state=state,
                        created_at=now,
                        updated_at=now,
                        tenant_id=tenant_id,
                        team_id="support",
                    ),
                    ctx=ctx,
                )
                runs += 1
            self._cost_service.record(
                CostEvent(
                    event_id=f"demo-{tenant_id}-cost",
                    tenant_id=tenant_id,
                    team_id="support",
                    workload_id=_TENANT_WORKLOADS[tenant_id][0][0],
                    cost_type=CostType.LLM,
                    model="openai/gpt-4o/2024-08-06",
                    cost_usd=1.25,
                    completed=True,
                    occurred_at=now,
                ),
                ctx=ctx,
            )
            events += 1
        return DemoSeedReport(
            profile=profile,
            tenant_ids=[tenant_id for tenant_id, _ in tenants],
            workloads=workloads,
            runs=runs,
            metering_events=events,
            seeded_at=now,
        )
