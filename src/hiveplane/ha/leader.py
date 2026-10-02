"""Leader election for the controller loop (M48-01..M48-03).

A lease table (in-memory or PostgreSQL) grants a single active leader. Every
successful acquire bumps a **fencing epoch** included in leader actions, so a
stale leader that resumes after failover cannot double-reconcile. Standbys poll
until the lease expires or is released.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class LeaderError(Exception):
    """Base class for leader-election failures."""


class StaleLeaderError(LeaderError):
    """Raised when an action carries a superseded fencing epoch (split-brain)."""

    def __init__(self, epoch: int) -> None:
        super().__init__(f"fencing epoch {epoch} is stale")
        self.epoch = epoch


class LeaderLease(BaseModel):
    """The current holder of a controller lock."""

    model_config = ConfigDict(extra="forbid")

    lock_key: str = Field(min_length=1)
    leader_id: str = Field(min_length=1)
    epoch: int = Field(ge=1)
    acquired_at: AwareDatetime
    renewed_at: AwareDatetime
    expires_at: AwareDatetime


class LeaderStore(Protocol):
    """Coordination primitive for leader leases."""

    def try_acquire(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None: ...

    def renew(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None: ...

    def release(self, lock_key: str, leader_id: str) -> None: ...

    def get(self, lock_key: str) -> LeaderLease | None: ...

    def clear(self) -> None: ...


class InMemoryLeaderStore:
    """A process-local leader store for single-process and test use."""

    def __init__(self) -> None:
        self._leases: dict[str, LeaderLease] = {}
        self._epochs: dict[str, int] = {}

    def try_acquire(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None:
        current = self._leases.get(lock_key)
        if current is not None and current.expires_at > now and current.leader_id != leader_id:
            return None
        epoch = self._epochs.get(lock_key, 0) + 1
        self._epochs[lock_key] = epoch
        lease = LeaderLease(
            lock_key=lock_key,
            leader_id=leader_id,
            epoch=epoch,
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._leases[lock_key] = lease
        return lease

    def renew(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None:
        current = self._leases.get(lock_key)
        if current is None or current.leader_id != leader_id or current.expires_at <= now:
            return None
        current.renewed_at = now
        current.expires_at = now + timedelta(seconds=ttl_seconds)
        self._leases[lock_key] = current
        return current

    def release(self, lock_key: str, leader_id: str) -> None:
        current = self._leases.get(lock_key)
        if current is not None and current.leader_id == leader_id:
            del self._leases[lock_key]

    def get(self, lock_key: str) -> LeaderLease | None:
        lease = self._leases.get(lock_key)
        return None if lease is None else lease.model_copy(deep=True)

    def clear(self) -> None:
        self._leases.clear()
        self._epochs.clear()


class LeaderElector:
    """Coordinates a single active controller through a lease table."""

    def __init__(
        self,
        store: LeaderStore,
        *,
        leader_id: str | None = None,
        lock_key: str = "reconcile",
        ttl_seconds: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._leader_id = leader_id or f"replica-{uuid4().hex[:8]}"
        self._lock_key = lock_key
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def leader_id(self) -> str:
        """Return this replica's id."""
        return self._leader_id

    def acquire(self) -> bool:
        """Attempt to become the leader; True if this replica now leads."""
        lease = self._store.try_acquire(
            self._lock_key, self._leader_id, ttl_seconds=self._ttl, now=self._clock()
        )
        return lease is not None and lease.leader_id == self._leader_id

    def renew(self) -> bool:
        """Renew the lease if this replica still leads."""
        lease = self._store.renew(
            self._lock_key, self._leader_id, ttl_seconds=self._ttl, now=self._clock()
        )
        return lease is not None

    def release(self) -> None:
        """Release leadership so a standby can promote immediately."""
        self._store.release(self._lock_key, self._leader_id)

    def current(self) -> LeaderLease | None:
        """Return the current leader lease, if any (even if expired)."""
        return self._store.get(self._lock_key)

    def is_leader(self) -> bool:
        """Return True when this replica holds an unexpired lease."""
        current = self._store.get(self._lock_key)
        return (
            current is not None
            and current.leader_id == self._leader_id
            and current.expires_at > self._clock()
        )

    def epoch(self) -> int:
        """Return this replica's active fencing epoch, or 0 when not leader."""
        current = self._store.get(self._lock_key)
        if current is None or current.leader_id != self._leader_id:
            return 0
        return current.epoch

    def run_cycle(self, action: Callable[[int], object]) -> object | None:
        """Run a leader action under the current fence; ``None`` when standby.

        The action receives the fencing epoch; re-checking the fence immediately
        before acting prevents a stale leader from double-reconciling.
        """
        current = self._store.get(self._lock_key)
        now = self._clock()
        if (
            current is None
            or current.leader_id != self._leader_id
            or current.expires_at <= now
        ):
            return None
        return action(current.epoch)

    def check_fence(self, epoch: int) -> None:
        """Raise :class:`StaleLeaderError` unless ``epoch`` is the live fence."""
        current = self._store.get(self._lock_key)
        if current is None or current.epoch != epoch or current.expires_at <= self._clock():
            raise StaleLeaderError(epoch)
