"""Federation models (M59-08, stretch)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PlaneStatus(StrEnum):
    """Reachability of a registered remote plane."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNREACHABLE = "unreachable"


class RemotePlane(BaseModel):
    """A remote HivePlane registered with this control plane."""

    model_config = ConfigDict(extra="forbid")

    plane_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    base_url: str = Field(min_length=1, max_length=512)
    registered_at: AwareDatetime
    last_seen_at: AwareDatetime | None = None
    status: PlaneStatus = PlaneStatus.UNKNOWN


class AggregateEntry(BaseModel):
    """One remote plane's contribution to the aggregate fleet view."""

    model_config = ConfigDict(extra="forbid")

    plane_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    base_url: str = Field(min_length=1, max_length=512)
    status: PlaneStatus
    workload_count: int = Field(ge=0)


class AggregateView(BaseModel):
    """The aggregate view across all registered remote planes."""

    model_config = ConfigDict(extra="forbid")

    plane_count: int = Field(ge=0)
    total_workloads: int = Field(ge=0)
    entries: list[AggregateEntry]
