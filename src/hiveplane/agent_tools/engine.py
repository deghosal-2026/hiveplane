"""The agent-as-tool invoker: nested calls with propagation (M30-05, M30-06).

A nested call submits a child run carrying an ``AgentToolOrigin`` so the caller,
depth, and workload chain are attributed. The nested run passes the same
admission path (certification, policy, budget) as any run; the invoker
additionally enforces the depth limit and rejects cycles in the workload chain
before anything is submitted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from hiveplane.agent_tools.models import (
    AgentToolInvocation,
    AgentToolNotFoundError,
    InvocationDecision,
)
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.agent_tools.store import AgentToolStore
from hiveplane.core.run import AdmissionContext, AgentToolOrigin
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext

DEFAULT_MAX_DEPTH = 5


class _Run(Protocol):
    id: str
    cost_usd: float


class _RunService(Protocol):
    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue],
        agent_tool_origin: AgentToolOrigin | None,
        ctx: TenantContext,
    ) -> _Run: ...


class AgentToolInvoker:
    """Invokes certified workloads as nested tools, with guardrails."""

    def __init__(
        self,
        registry: AgentToolRegistry,
        run_service: _RunService,
        *,
        store: AgentToolStore | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        self._registry = registry
        self._runs = run_service
        self._store = store
        self._max_depth = max_depth
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"atool-{uuid.uuid4().hex[:12]}")

    @property
    def max_depth(self) -> int:
        """The maximum nested-call depth (caller depth + 1)."""
        return self._max_depth

    def invoke(
        self,
        tool_id: str,
        *,
        caller_run_id: str,
        task: dict[str, JsonValue] | None = None,
        context: AdmissionContext = AdmissionContext.STAGING,
        depth: int = 0,
        chain: list[str] | None = None,
        budget_remaining_usd: float | None = None,
        caller_cost_usd: float = 0.0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AgentToolInvocation:
        """Invoke ``tool_id`` as a nested run, or refuse with a reason."""
        chain = list(chain or [])
        try:
            workload = self._registry.resolve(tool_id)
        except AgentToolNotFoundError:
            return self._record(
                self._refuse(
                    tool_id,
                    workload=tool_id,
                    caller_run_id=caller_run_id,
                    depth=depth + 1,
                    chain=chain,
                    context=context,
                    reason="unknown agent tool",
                    ctx=ctx,
                ),
                ctx,
            )
        next_depth = depth + 1
        next_chain = [*chain, workload]
        if next_depth > self._max_depth:
            return self._record(
                self._refuse(
                    tool_id,
                    workload,
                    caller_run_id,
                    next_depth,
                    next_chain,
                    context,
                    f"depth limit {self._max_depth} exceeded",
                    ctx,
                ),
                ctx,
            )
        if workload in chain:
            return self._record(
                self._refuse(
                    tool_id,
                    workload,
                    caller_run_id,
                    next_depth,
                    next_chain,
                    context,
                    f"cycle detected in invocation chain: {next_chain}",
                    ctx,
                ),
                ctx,
            )
        decision = self._registry.admission(workload, context)
        if not decision.admitted:
            return self._record(
                self._refuse(
                    tool_id,
                    workload,
                    caller_run_id,
                    next_depth,
                    next_chain,
                    context,
                    decision.reason
                    or f"workload not certified for {context.value}",
                    ctx,
                ),
                ctx,
            )
        if budget_remaining_usd is not None and caller_cost_usd >= budget_remaining_usd:
            return self._record(
                self._refuse(
                    tool_id,
                    workload,
                    caller_run_id,
                    next_depth,
                    next_chain,
                    context,
                    "caller budget exhausted",
                    ctx,
                ),
                ctx,
            )
        origin = AgentToolOrigin(
            caller_run_id=caller_run_id,
            tool_id=tool_id,
            workload=workload,
            depth=next_depth,
            chain=next_chain,
        )
        try:
            run = self._runs.submit(
                workload=workload,
                caller=f"agent-tool:{caller_run_id}",
                context=context,
                task=dict(task or {}),
                agent_tool_origin=origin,
                ctx=ctx,
            )
        except RunAdmissionRefusedError as exc:
            return self._record(
                self._refuse(
                    tool_id,
                    workload,
                    caller_run_id,
                    next_depth,
                    next_chain,
                    context,
                    f"nested admission refused: {exc}",
                    ctx,
                ),
                ctx,
            )
        invocation = AgentToolInvocation(
            invocation_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            caller_run_id=caller_run_id,
            nested_run_id=run.id,
            tool_id=tool_id,
            workload=workload,
            depth=next_depth,
            chain=next_chain,
            context=context,
            decision=InvocationDecision.ALLOWED,
            cost_usd=run.cost_usd,
            created_at=self._clock(),
        )
        return self._record(invocation, ctx)

    def _refuse(
        self,
        tool_id: str,
        workload: str,
        caller_run_id: str,
        depth: int,
        chain: list[str],
        context: AdmissionContext,
        reason: str,
        ctx: TenantContext,
    ) -> AgentToolInvocation:
        return AgentToolInvocation(
            invocation_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            caller_run_id=caller_run_id,
            tool_id=tool_id,
            workload=workload,
            depth=depth,
            chain=chain,
            context=context,
            decision=InvocationDecision.REFUSED,
            reason=reason,
            created_at=self._clock(),
        )

    def _record(
        self, invocation: AgentToolInvocation, ctx: TenantContext
    ) -> AgentToolInvocation:
        if self._store is not None:
            self._store.save_invocation(invocation, ctx=ctx)
        return invocation
