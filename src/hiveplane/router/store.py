"""Router decision storage (M30-03).

Decisions are recorded so a route can be explained and audited after the fact.
The raw task is never persisted — only its digest, the classifier identity, the
ranked candidates, and the outcome.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import RouterDecisionRow
from hiveplane.router.models import RouteDecision
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class RouterStore(Protocol):
    """Storage interface for router decisions."""

    def save_decision(
        self, decision: RouteDecision, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_decision(
        self, decision_id: str, *, ctx: TenantContext = ...
    ) -> RouteDecision | None: ...

    def list_decisions(
        self, *, ctx: TenantContext = ...
    ) -> list[RouteDecision]: ...

    def clear(self) -> None: ...


class InMemoryRouterStore:
    """A process-local, thread-safe router-decision store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._decisions: dict[str, tuple[RouteDecision, str]] = {}

    def save_decision(
        self, decision: RouteDecision, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(decision.tenant_id)
        with self._lock:
            self._decisions[decision.decision_id] = (
                decision.model_copy(deep=True),
                decision.tenant_id,
            )

    def get_decision(
        self, decision_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RouteDecision | None:
        with self._lock:
            entry = self._decisions.get(decision_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_decisions(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RouteDecision]:
        with self._lock:
            decisions = [
                decision
                for decision, tenant in self._decisions.values()
                if ctx.scopes(tenant)
            ]
            decisions.sort(key=lambda decision: (decision.created_at, decision.decision_id))
            return [decision.model_copy(deep=True) for decision in decisions]

    def clear(self) -> None:
        with self._lock:
            self._decisions.clear()


class PostgresRouterStore:
    """A durable router-decision store backed by PostgreSQL (M30-03)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_decision(
        self, decision: RouteDecision, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(decision.tenant_id)
        with self._session.begin() as session:
            row = session.get(RouterDecisionRow, decision.decision_id)
            payload = decision.model_dump(mode="json")
            if row is None:
                session.add(
                    RouterDecisionRow(
                        decision_id=decision.decision_id,
                        tenant_id=decision.tenant_id,
                        task_hash=decision.task_hash,
                        outcome=decision.outcome.value,
                        chosen=decision.chosen,
                        created_at=decision.created_at,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id,
                        f"cannot modify router decision {decision.decision_id!r}",
                    )
                row.outcome = decision.outcome.value
                row.chosen = decision.chosen
                row.payload = payload

    def get_decision(
        self, decision_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RouteDecision | None:
        with self._session() as session:
            row = session.get(RouterDecisionRow, decision_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return RouteDecision.model_validate(row.payload)

    def list_decisions(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RouteDecision]:
        statement = select(RouterDecisionRow).order_by(
            RouterDecisionRow.created_at, RouterDecisionRow.decision_id
        )
        if not ctx.is_system:
            statement = statement.where(RouterDecisionRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                RouteDecision.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(RouterDecisionRow))


def build_router_store(settings: Settings | None = None) -> RouterStore:
    """Build the configured router-decision store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresRouterStore(create_engine_from_settings(resolved))
    return InMemoryRouterStore()
