"""Models for fleet-event subscriptions and events (M56-05)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class FleetEventKind(StrEnum):
    """The families of fleet events an operator can subscribe to."""

    RUN = "run"
    APPROVAL = "approval"
    DRIFT = "drift"
    TRIGGER = "trigger"


class EventSubscription(BaseModel):
    """A tenant's webhook subscription to fleet events."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    kinds: list[FleetEventKind] = Field(min_length=1)
    active: bool = True
    created_at: AwareDatetime


class FleetEvent(BaseModel):
    """A fleet event to deliver to matching subscriptions."""

    model_config = ConfigDict(extra="forbid")

    kind: FleetEventKind
    event_type: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: AwareDatetime
