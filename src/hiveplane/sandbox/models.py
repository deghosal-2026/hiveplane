"""Sandbox instance models (D11, DD-14)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict

from hiveplane.core.sandbox import EgressMode, ResourceCaps


class SandboxStatus(StrEnum):
    """Lifecycle state of a sandbox instance."""

    PROVISIONING = "provisioning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DESTROYED = "destroyed"


class SandboxInstance(BaseModel):
    """A provisioned sandbox and its lifecycle metadata."""

    model_config = ConfigDict(extra="forbid")

    sandbox_id: str
    run_id: str
    workload: str
    status: SandboxStatus
    resource_caps: ResourceCaps | None = None
    egress_mode: EgressMode = EgressMode.RESTRICTED
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
