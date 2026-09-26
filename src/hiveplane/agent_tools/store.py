"""Agent-tool invocation storage (M30-05).

Every nested call — allowed or refused — is recorded for attribution and audit.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.agent_tools.models import AgentToolInvocation
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import AgentToolInvocationRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class AgentToolStore(Protocol):
    """Storage interface for agent-tool invocations."""

    def save_invocation(
        self, invocation: AgentToolInvocation, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_invocation(
        self, invocation_id: str, *, ctx: TenantContext = ...
    ) -> AgentToolInvocation | None: ...

    def list_invocations(
        self, *, caller_run_id: str | None = None, ctx: TenantContext = ...
    ) -> list[AgentToolInvocation]: ...

    def clear(self) -> None: ...


class InMemoryAgentToolStore:
    """A process-local, thread-safe agent-tool invocation store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._invocations: dict[str, tuple[AgentToolInvocation, str]] = {}

    def save_invocation(
        self, invocation: AgentToolInvocation, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(invocation.tenant_id)
        with self._lock:
            self._invocations[invocation.invocation_id] = (
                invocation.model_copy(deep=True),
                invocation.tenant_id,
            )

    def get_invocation(
        self, invocation_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> AgentToolInvocation | None:
        with self._lock:
            entry = self._invocations.get(invocation_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_invocations(
        self, *, caller_run_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AgentToolInvocation]:
        with self._lock:
            invocations = [
                invocation
                for invocation, tenant in self._invocations.values()
                if ctx.scopes(tenant)
                and (caller_run_id is None or invocation.caller_run_id == caller_run_id)
            ]
            invocations.sort(
                key=lambda invocation: (invocation.created_at, invocation.invocation_id)
            )
            return [invocation.model_copy(deep=True) for invocation in invocations]

    def clear(self) -> None:
        with self._lock:
            self._invocations.clear()


class PostgresAgentToolStore:
    """A durable agent-tool invocation store backed by PostgreSQL (M30-05)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_invocation(
        self, invocation: AgentToolInvocation, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(invocation.tenant_id)
        payload = invocation.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(AgentToolInvocationRow, invocation.invocation_id)
            if row is None:
                session.add(
                    AgentToolInvocationRow(
                        invocation_id=invocation.invocation_id,
                        tenant_id=invocation.tenant_id,
                        caller_run_id=invocation.caller_run_id,
                        nested_run_id=invocation.nested_run_id,
                        workload=invocation.workload,
                        depth=invocation.depth,
                        decision=invocation.decision.value,
                        created_at=invocation.created_at,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id,
                        f"cannot modify agent-tool invocation {invocation.invocation_id!r}",
                    )
                row.nested_run_id = invocation.nested_run_id
                row.decision = invocation.decision.value
                row.payload = payload

    def get_invocation(
        self, invocation_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> AgentToolInvocation | None:
        with self._session() as session:
            row = session.get(AgentToolInvocationRow, invocation_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return AgentToolInvocation.model_validate(row.payload)

    def list_invocations(
        self, *, caller_run_id: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AgentToolInvocation]:
        statement = select(AgentToolInvocationRow).order_by(
            AgentToolInvocationRow.created_at, AgentToolInvocationRow.invocation_id
        )
        if caller_run_id is not None:
            statement = statement.where(
                AgentToolInvocationRow.caller_run_id == caller_run_id
            )
        if not ctx.is_system:
            statement = statement.where(AgentToolInvocationRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                AgentToolInvocation.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(AgentToolInvocationRow))


def build_agent_tool_store(settings: Settings | None = None) -> AgentToolStore:
    """Build the configured agent-tool store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresAgentToolStore(create_engine_from_settings(resolved))
    return InMemoryAgentToolStore()
