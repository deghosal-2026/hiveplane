"""Observed fleet state and the observer that reads it (M26-02, D22).

Observation is read-only and goes through existing service APIs: the registry
for workloads and the policy-pack store for pack versions. The differ compares
this snapshot against desired state; the controller never writes during observe.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.policy.packs import PolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class ObservedState(BaseModel):
    """A read-only snapshot of actual fleet state for one reconcile pass."""

    model_config = ConfigDict(extra="forbid")

    workloads: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    policy_versions: dict[str, str] = Field(default_factory=dict)


class Observer(Protocol):
    """Reads actual fleet state from the control plane's stores."""

    def snapshot(self, *, ctx: TenantContext = ...) -> ObservedState: ...


class ServiceObserver:
    """Observes state through the registry and policy-pack services."""

    def __init__(
        self,
        registry: RegistryService,
        policy_packs: PolicyPackStore | None = None,
    ) -> None:
        self._registry = registry
        self._policy_packs = policy_packs

    def snapshot(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> ObservedState:
        """Return normalized workloads and policy-pack versions."""
        workloads = {
            record.name: record.manifest.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
            for record in self._registry.list_workloads()
        }
        versions: dict[str, str] = {}
        if self._policy_packs is not None:
            for pack in self._policy_packs.list_packs(ctx=ctx):
                versions[pack.metadata.name] = pack.metadata.version
        return ObservedState(workloads=workloads, policy_versions=versions)
