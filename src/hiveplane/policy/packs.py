"""Policy pack storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import PolicyPack
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class PolicyPackStore(Protocol):
    """Storage for team policy packs, scoped by the acting tenant context."""

    def save(self, pack: PolicyPack, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PolicyPack | None: ...

    def list_packs(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PolicyPack]: ...

    def for_team(
        self, team: str | None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PolicyPack]: ...


class InMemoryPolicyPackStore:
    """A process-local, thread-safe policy pack store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packs: dict[str, PolicyPack] = {}

    def save(self, pack: PolicyPack, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        """Register a pack, rejecting a duplicate name."""
        ctx.require(pack.metadata.tenant_id)
        with self._lock:
            name = pack.metadata.name
            if name in self._packs:
                raise PolicyPackAlreadyExistsError(name)
            self._packs[name] = pack.model_copy(deep=True)

    def get(self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> PolicyPack | None:
        """Return a pack by name."""
        with self._lock:
            pack = self._packs.get(name)
            if pack is None or not ctx.scopes(pack.metadata.tenant_id):
                return None
            return pack.model_copy(deep=True)

    def list_packs(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[PolicyPack]:
        """Return the tenant's packs, ordered by name."""
        with self._lock:
            ordered = sorted(
                (pack for pack in self._packs.values() if ctx.scopes(pack.metadata.tenant_id)),
                key=lambda pack: pack.metadata.name,
            )
            return [pack.model_copy(deep=True) for pack in ordered]

    def for_team(
        self, team: str | None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PolicyPack]:
        """Return the tenant's packs belonging to a team."""
        if team is None:
            return []
        return [
            pack
            for pack in self.list_packs(ctx=ctx)
            if pack.metadata.team == team
        ]
