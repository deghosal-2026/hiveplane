"""Agent2Agent (A2A) interop adapter (M30-07, stretch).

Behind ``HIVEPLANE_A2A__ENABLED``, this maps inbound A2A tasks onto the router
and run lifecycle, and exposes certified workloads as A2A agent cards. Outbound
trust is limited to registered planes. A2A is explicitly a stretch and must not
gate M30.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.core.run import AdmissionContext
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.registry.errors import AdmissionRefusedError
from hiveplane.router.engine import RouterEngine
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class A2ASkill(BaseModel):
    """One skill advertised by an A2A agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class A2AAgentCard(BaseModel):
    """A minimal A2A agent card for a certified workload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    url: str = Field(min_length=1)
    version: str = "1.0.0"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    skills: list[A2ASkill] = Field(default_factory=list)


class A2ATaskRequest(BaseModel):
    """An inbound A2A task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    context: AdmissionContext = AdmissionContext.STAGING
    plane_id: str | None = None


class A2ATaskResult(BaseModel):
    """The outcome of handling an inbound A2A task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    state: str
    target: str | None = None
    run_id: str | None = None
    reason: str | None = None


class _Run(Protocol):
    id: str


class _RunService(Protocol):
    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue],
        ctx: TenantContext,
    ) -> _Run: ...


class A2ANotEnabledError(Exception):
    """Raised when the A2A adapter is used while disabled."""


class A2AAdapter:
    """Maps inbound A2A tasks to routing and run submission."""

    def __init__(
        self,
        registry: AgentToolRegistry,
        run_service: _RunService,
        *,
        router: RouterEngine | None = None,
        plane_id: str = "hiveplane",
        allowed_planes: list[str] | None = None,
    ) -> None:
        self._registry = registry
        self._runs = run_service
        self._router = router
        self._plane_id = plane_id
        self._allowed = set(allowed_planes or [])

    @property
    def plane_id(self) -> str:
        """This plane's identity."""
        return self._plane_id

    def trusts(self, plane_id: str | None) -> bool:
        """Whether an inbound/outbound plane is trusted."""
        if plane_id is None or plane_id == self._plane_id:
            return True
        return plane_id in self._allowed

    def agent_cards(
        self,
        base_url: str,
        *,
        context: AdmissionContext = AdmissionContext.STAGING,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[A2AAgentCard]:
        """Expose certified workloads as A2A agent cards."""
        cards: list[A2AAgentCard] = []
        for tool in self._registry.list_tools(context, ctx=ctx):
            cards.append(
                A2AAgentCard(
                    name=tool.workload,
                    description=tool.description,
                    url=f"{base_url.rstrip('/')}/a2a/agents/{tool.workload}",
                    capabilities={"streaming": False, "pushNotifications": False},
                    skills=[
                        A2ASkill(
                            id=tool.tool_id,
                            name=tool.workload,
                            description=tool.description,
                        )
                    ],
                )
            )
        return cards

    def handle_task(
        self, request: A2ATaskRequest, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> A2ATaskResult:
        """Route an inbound A2A task and submit the chosen workload."""
        if not self.trusts(request.plane_id):
            return A2ATaskResult(
                task_id=request.task_id,
                state="refused",
                reason=f"plane {request.plane_id!r} is not registered",
            )
        if self._router is None:
            return A2ATaskResult(
                task_id=request.task_id,
                state="refused",
                reason="router is not enabled",
            )
        decision = self._router.route(
            request.message, context=request.context, ctx=ctx
        )
        if not decision.routed or decision.chosen is None:
            return A2ATaskResult(
                task_id=request.task_id,
                state="refused",
                reason=decision.reason.value if decision.reason else "refused",
            )
        try:
            run = self._runs.submit(
                workload=decision.chosen,
                caller=f"a2a:{request.plane_id or self._plane_id}",
                context=request.context,
                task={"task_id": request.task_id, "message": request.message},
                ctx=ctx,
            )
        except (AdmissionRefusedError, RunAdmissionRefusedError) as exc:
            return A2ATaskResult(
                task_id=request.task_id,
                state="refused",
                target=decision.chosen,
                reason=str(exc),
            )
        return A2ATaskResult(
            task_id=request.task_id,
            state="submitted",
            target=decision.chosen,
            run_id=run.id,
        )
