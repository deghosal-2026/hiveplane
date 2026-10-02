"""Execution sandbox models (DD-14)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

CLOUD_METADATA_ENDPOINTS: tuple[str, ...] = ("169.254.169.254",)


class EgressMode(StrEnum):
    """Network egress policy for a sandboxed run."""

    OPEN = "open"
    RESTRICTED = "restricted"
    NONE = "none"


class FilesystemMode(StrEnum):
    """Filesystem isolation mode."""

    ISOLATED = "isolated"


class ResourceCaps(BaseModel):
    """Per-run resource ceilings."""

    model_config = ConfigDict(extra="forbid")

    memory_mb: int = Field(gt=0)
    cpu_cores: float = Field(gt=0)
    wall_clock_s: int = Field(gt=0)


class EgressSpec(BaseModel):
    """Network egress allow/deny lists and mode (legacy host-string form)."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    mode: EgressMode = EgressMode.RESTRICTED


class EgressRule(BaseModel):
    """A host (exact or ``*.suffix`` wildcard) and optional port."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)


class NetworkSpec(BaseModel):
    """Deny-by-default egress allow/deny rules (M39-05)."""

    model_config = ConfigDict(extra="forbid")

    egress: EgressMode = EgressMode.RESTRICTED
    allow: list[EgressRule] = Field(default_factory=list)
    deny: list[EgressRule] = Field(default_factory=list)


class SandboxSpec(BaseModel):
    """Execution isolation configuration for a workload."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    resource_caps: ResourceCaps | None = None
    egress: EgressSpec = Field(default_factory=EgressSpec)
    network: NetworkSpec | None = None
    filesystem: FilesystemMode = FilesystemMode.ISOLATED

    @model_validator(mode="after")
    def _enforce_invariants(self) -> SandboxSpec:
        if self.enabled and self.resource_caps is None:
            raise ValueError("sandbox.resource_caps is required when sandbox.enabled is true")
        for endpoint in CLOUD_METADATA_ENDPOINTS:
            if endpoint not in self.egress.deny:
                self.egress.deny.append(endpoint)
            if self.network is not None and all(
                rule.host != endpoint for rule in self.network.deny
            ):
                self.network.deny.append(EgressRule(host=endpoint))
        return self
