"""PostgreSQL leader-lease store (M48-01)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, delete

from hiveplane.config import Settings, get_settings
from hiveplane.ha.leader import LeaderLease, LeaderStore
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import LeaderLeaseRow

#: Sentinel leader id marking a released lease row retained for epoch monotonicity.
_RELEASED_LEADER = "__released__"

#: A release tombstone never blocks acquisition because its expiry is in the past.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PostgresLeaderStore:
    """A durable, row-locked leader lease store (single active leader)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def try_acquire(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None:
        with self._session.begin() as session:
            row = session.get(LeaderLeaseRow, lock_key, with_for_update=True)
            if row is not None:
                existing = LeaderLease.model_validate(row.payload)
                if existing.expires_at > now and existing.leader_id != leader_id:
                    return None
                epoch = existing.epoch + 1
            else:
                epoch = 1
            lease = LeaderLease(
                lock_key=lock_key,
                leader_id=leader_id,
                epoch=epoch,
                acquired_at=now,
                renewed_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            payload = lease.model_dump(mode="json")
            if row is None:
                session.add(
                    LeaderLeaseRow(
                        lock_key=lock_key,
                        leader_id=leader_id,
                        epoch=epoch,
                        expires_at=lease.expires_at,
                        payload=payload,
                    )
                )
            else:
                row.leader_id = leader_id
                row.epoch = epoch
                row.expires_at = lease.expires_at
                row.payload = payload
            return lease

    def renew(
        self, lock_key: str, leader_id: str, *, ttl_seconds: int, now: datetime
    ) -> LeaderLease | None:
        with self._session.begin() as session:
            row = session.get(LeaderLeaseRow, lock_key, with_for_update=True)
            if row is None:
                return None
            lease = LeaderLease.model_validate(row.payload)
            if lease.leader_id != leader_id or lease.expires_at <= now:
                return None
            lease.renewed_at = now
            lease.expires_at = now + timedelta(seconds=ttl_seconds)
            row.expires_at = lease.expires_at
            row.payload = lease.model_dump(mode="json")
            return lease

    def release(self, lock_key: str, leader_id: str) -> None:
        with self._session.begin() as session:
            row = session.get(LeaderLeaseRow, lock_key, with_for_update=True)
            if row is not None and row.leader_id == leader_id:
                lease = LeaderLease.model_validate(row.payload)
                tombstone = lease.model_copy(
                    update={"leader_id": _RELEASED_LEADER, "expires_at": _EPOCH}
                )
                row.leader_id = _RELEASED_LEADER
                row.expires_at = _EPOCH
                row.payload = tombstone.model_dump(mode="json")

    def get(self, lock_key: str) -> LeaderLease | None:
        with self._session() as session:
            row = session.get(LeaderLeaseRow, lock_key)
            if row is None or row.leader_id == _RELEASED_LEADER:
                return None
            return LeaderLease.model_validate(row.payload)

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(LeaderLeaseRow))


def build_leader_store(settings: Settings | None = None) -> LeaderStore:
    """Build the configured leader store (in-memory by default)."""
    from hiveplane.ha.leader import InMemoryLeaderStore

    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresLeaderStore(create_engine_from_settings(resolved))
    return InMemoryLeaderStore()
