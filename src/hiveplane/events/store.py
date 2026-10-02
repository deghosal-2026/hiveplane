"""Persistence for fleet-event subscriptions (M56-05)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.events.models import EventSubscription
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import EventSubscriptionRow
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


class EventSubscriptionStore(Protocol):
    """Storage interface for fleet-event subscriptions."""

    def add(
        self, subscription: EventSubscription, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EventSubscription | None: ...

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EventSubscription]: ...

    def remove(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None: ...

    def clear(self) -> None: ...


class InMemoryEventSubscriptionStore:
    """A process-local event-subscription store."""

    def __init__(self) -> None:
        self._subscriptions: dict[tuple[str, str], EventSubscription] = {}

    def add(
        self, subscription: EventSubscription, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(subscription.tenant_id)
        key = (subscription.tenant_id, subscription.subscription_id)
        self._subscriptions[key] = subscription.model_copy(deep=True)

    def get(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EventSubscription | None:
        if not ctx.scopes(tenant_id):
            return None
        record = self._subscriptions.get((tenant_id, subscription_id))
        return None if record is None else record.model_copy(deep=True)

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EventSubscription]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for (scope, _), record in self._subscriptions.items()
            if scope == tenant_id
        ]
        records.sort(key=lambda record: record.subscription_id)
        return records

    def remove(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        if not ctx.scopes(tenant_id):
            return
        self._subscriptions.pop((tenant_id, subscription_id), None)

    def clear(self) -> None:
        self._subscriptions.clear()


class PostgresEventSubscriptionStore:
    """A durable event-subscription store backed by PostgreSQL (M56-05)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add(
        self, subscription: EventSubscription, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(subscription.tenant_id)
        with self._session.begin() as session:
            row = session.get(EventSubscriptionRow, subscription.subscription_id)
            payload = subscription.model_dump(mode="json")
            if row is None:
                session.add(
                    EventSubscriptionRow(
                        subscription_id=subscription.subscription_id,
                        url=subscription.url,
                        active=subscription.active,
                        created_at=subscription.created_at,
                        tenant_id=subscription.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if row.tenant_id != subscription.tenant_id:
                    raise TenantScopeError(
                        ctx.tenant_id,
                        f"cannot modify subscription {subscription.subscription_id!r}",
                    )
                row.url = subscription.url
                row.active = subscription.active
                row.created_at = subscription.created_at
                row.tenant_id = subscription.tenant_id
                row.payload = payload

    def get(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EventSubscription | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(EventSubscriptionRow, subscription_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return EventSubscription.model_validate(row.payload)

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EventSubscription]:
        if not ctx.scopes(tenant_id):
            return []
        statement = (
            select(EventSubscriptionRow)
            .where(EventSubscriptionRow.tenant_id == tenant_id)
            .order_by(EventSubscriptionRow.subscription_id)
        )
        with self._session() as session:
            return [
                EventSubscription.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def remove(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        if not ctx.scopes(tenant_id):
            return
        with self._session.begin() as session:
            row = session.get(EventSubscriptionRow, subscription_id)
            if row is not None and row.tenant_id == tenant_id:
                session.delete(row)

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(EventSubscriptionRow))


def build_event_subscription_store(
    settings: Settings | None = None,
) -> EventSubscriptionStore:
    """Build the configured event-subscription store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresEventSubscriptionStore(create_engine_from_settings(resolved))
    return InMemoryEventSubscriptionStore()
