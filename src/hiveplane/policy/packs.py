"""Policy pack storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import PolicyPack


class PolicyPackStore(Protocol):
    """Storage for team policy packs."""

    def save(self, pack: PolicyPack) -> None: ...

    def get(self, name: str) -> PolicyPack | None: ...

    def list_packs(self) -> list[PolicyPack]: ...

    def for_team(self, team: str | None) -> list[PolicyPack]: ...


class InMemoryPolicyPackStore:
    """A process-local, thread-safe policy pack store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packs: dict[str, PolicyPack] = {}

    def save(self, pack: PolicyPack) -> None:
        """Register a pack, rejecting a duplicate name."""
        with self._lock:
            name = pack.metadata.name
            if name in self._packs:
                raise PolicyPackAlreadyExistsError(name)
            self._packs[name] = pack.model_copy(deep=True)

    def get(self, name: str) -> PolicyPack | None:
        """Return a pack by name."""
        with self._lock:
            pack = self._packs.get(name)
            return pack.model_copy(deep=True) if pack is not None else None

    def list_packs(self) -> list[PolicyPack]:
        """Return all packs, ordered by name."""
        with self._lock:
            ordered = sorted(self._packs.values(), key=lambda pack: pack.metadata.name)
            return [pack.model_copy(deep=True) for pack in ordered]

    def for_team(self, team: str | None) -> list[PolicyPack]:
        """Return packs belonging to a team."""
        if team is None:
            return []
        return [pack for pack in self.list_packs() if pack.metadata.team == team]
