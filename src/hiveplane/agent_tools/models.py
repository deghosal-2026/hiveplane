"""Agent-as-tool models: exposed agents, invocations, and refusals (M30-04..06)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.run import AdmissionContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID

TOOL_PREFIX = "agent."


class InvocationDecision(StrEnum):
    """Whether a nested agent-as-tool call was allowed or refused."""

    ALLOWED = "allowed"
    REFUSED = "refused"


class AgentTool(BaseModel):
    """A certified workload exposed as a callable tool."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    description: str = ""


class AgentToolInvocation(BaseModel):
    """The recorded outcome of one nested agent-as-tool call (M30-05)."""

    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    caller_run_id: str | None = None
    nested_run_id: str | None = None
    tool_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    depth: int = Field(ge=1)
    chain: list[str] = Field(default_factory=list)
    context: AdmissionContext
    decision: InvocationDecision
    reason: str | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    created_at: AwareDatetime

    @property
    def allowed(self) -> bool:
        """Whether the nested call was permitted."""
        return self.decision is InvocationDecision.ALLOWED


class AgentToolInvocationRequest(BaseModel):
    """An API request to invoke a certified workload as a tool."""

    model_config = ConfigDict(extra="forbid")

    caller_run_id: str = Field(min_length=1)
    task: dict[str, JsonValue] = Field(default_factory=dict)
    context: AdmissionContext = AdmissionContext.STAGING
    depth: int = Field(default=0, ge=0)
    chain: list[str] = Field(default_factory=list)
    budget_remaining_usd: float | None = Field(default=None, ge=0.0)
    caller_cost_usd: float = Field(default=0.0, ge=0.0)


class AgentToolNotFoundError(Exception):
    """Raised when a tool id does not name a certified workload."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(f"agent tool {tool_id!r} not found")
        self.tool_id = tool_id


class AgentToolRefusedError(Exception):
    """Raised when a nested call is refused before submission."""

    def __init__(self, tool_id: str, reason: str) -> None:
        super().__init__(f"agent tool {tool_id!r} refused: {reason}")
        self.tool_id = tool_id
        self.reason = reason
