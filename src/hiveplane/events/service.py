"""Subscribe to fleet events and deliver them to webhooks (M56-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from hiveplane.events.models import (
    EventSubscription,
    FleetEvent,
    FleetEventKind,
)
from hiveplane.events.store import EventSubscriptionStore
from hiveplane.persistence.audit import AuditLog
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext, context_for_run

#: Deliver a JSON payload to a subscription URL (best effort).
Sender = Callable[[str, dict[str, Any]], None]


def new_subscription_id() -> str:
    """Return a fresh opaque subscription id."""
    return f"sub-{uuid4().hex[:16]}"


class FleetEventService:
    """Manages event subscriptions and delivers matching events."""

    def __init__(
        self,
        store: EventSubscriptionStore,
        *,
        sender: Sender | None = None,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or new_subscription_id

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def subscribe(
        self,
        *,
        tenant_id: str,
        url: str,
        kinds: list[FleetEventKind],
        actor: str = "operator",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EventSubscription:
        """Create a webhook subscription for the given event kinds."""
        subscription = EventSubscription(
            subscription_id=self._id_factory(),
            tenant_id=tenant_id,
            url=url,
            kinds=kinds,
            created_at=self._clock(),
        )
        self._store.add(subscription, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "events.subscribed",
                subscription.subscription_id,
                detail=f"url={url} kinds={','.join(kind.value for kind in kinds)}",
            )
        return subscription

    def unsubscribe(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        actor: str = "operator",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Remove a subscription."""
        self._store.remove(subscription_id, tenant_id=tenant_id, ctx=ctx)
        if self._audit is not None:
            self._audit.append(actor, "events.unsubscribed", subscription_id)

    def subscriptions(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[EventSubscription]:
        """Return a tenant's subscriptions."""
        return self._store.list(tenant_id=tenant_id, ctx=ctx)

    def get(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EventSubscription | None:
        """Return a subscription by id."""
        return self._store.get(subscription_id, tenant_id=tenant_id, ctx=ctx)

    def publish(
        self, event: FleetEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[str]:
        """Deliver an event to matching subscriptions; never raises.

        Returns the URLs that accepted the delivery. A failing destination is
        skipped so one broken subscriber cannot stall event delivery.
        """
        delivered: list[str] = []
        if self._sender is None:
            return delivered
        payload = event.model_dump(mode="json")
        scope = ctx if ctx.scopes(event.tenant_id) else context_for_run(event.tenant_id)
        for subscription in self._store.list(tenant_id=event.tenant_id, ctx=scope):
            if not subscription.active or event.kind not in subscription.kinds:
                continue
            try:
                self._sender(subscription.url, payload)
            except Exception:
                continue
            delivered.append(subscription.url)
        return delivered
