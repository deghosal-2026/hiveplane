from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from planner_critic.reason_codes import (
    CONCURRENT_RESOURCE_CONFLICT,
    RESOURCE_LOCKED_BY_CONCURRENT_EXECUTION,
    STATE_VIEW_STALE,
)

logger = logging.getLogger(__name__)


class LockStrategy(StrEnum):
    WAIT = "wait"
    FAIL_FAST = "fail_fast"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class StateSnapshot:
    version: str
    captured_at: datetime
    snapshot: dict[str, Any]


class StateView:
    def __init__(self, snapshot: StateSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def version(self) -> str:
        return self._snapshot.version

    @property
    def captured_at(self) -> datetime:
        return self._snapshot.captured_at

    def read(self, key: str) -> Any | None:
        return self._snapshot.snapshot.get(key)

    def is_stale(self, current_snapshot: StateSnapshot) -> bool:
        if self._snapshot.version != current_snapshot.version:
            return True
        return self._snapshot.captured_at < current_snapshot.captured_at

    @property
    def reason_code(self) -> str:
        return STATE_VIEW_STALE


@dataclass
class ResourceLock:
    resource_uri: str
    plan_id: str
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lock_timeout: float = 30.0
    _released: bool = False

    @property
    def is_expired(self) -> bool:
        elapsed = (datetime.now(UTC) - self.acquired_at).total_seconds()
        return elapsed > self.lock_timeout

    def release(self) -> None:
        self._released = True

    @property
    def is_active(self) -> bool:
        return not self._released and not self.is_expired


class StateLock:
    def __init__(
        self,
        strategy: LockStrategy = LockStrategy.WAIT,
        lock_timeout: float = 30.0,
        wait_deadline: float = 30.0,
    ) -> None:
        self._strategy = strategy
        self._lock_timeout = lock_timeout
        self._wait_deadline = wait_deadline
        self._locks: dict[str, ResourceLock] = {}
        self._lock_obj = threading.Lock()

    def acquire(self, resource_uri: str, plan_id: str) -> str | None:
        self._lock_obj.acquire()
        existing = self._locks.get(resource_uri)
        if existing is not None and existing.is_active:
            reason = f"{RESOURCE_LOCKED_BY_CONCURRENT_EXECUTION}"
            logger.info(
                "state lock: %s is locked by plan %s — strategy=%s",
                resource_uri,
                existing.plan_id,
                self._strategy.value,
            )
            if self._strategy is LockStrategy.FAIL_FAST:
                self._lock_obj.release()
                return CONCURRENT_RESOURCE_CONFLICT
            if self._strategy is LockStrategy.ESCALATE:
                self._lock_obj.release()
                return reason
            if self._strategy is LockStrategy.WAIT:
                self._lock_obj.release()
                import time as _time

                deadline = _time.monotonic() + self._wait_deadline
                while _time.monotonic() < deadline:
                    _time.sleep(0.5)
                    self._lock_obj.acquire()
                    existing = self._locks.get(resource_uri)
                    if existing is None or not existing.is_active:
                        lock = ResourceLock(
                            resource_uri=resource_uri,
                            plan_id=plan_id,
                            lock_timeout=self._lock_timeout,
                        )
                        self._locks[resource_uri] = lock
                        self._lock_obj.release()
                        logger.info("state lock: acquired %s for plan %s", resource_uri, plan_id)
                        return None
                    self._lock_obj.release()
                return reason  # timeout
        lock = ResourceLock(
            resource_uri=resource_uri,
            plan_id=plan_id,
            lock_timeout=self._lock_timeout,
        )
        self._locks[resource_uri] = lock
        self._lock_obj.release()
        logger.info("state lock: acquired %s for plan %s", resource_uri, plan_id)
        return None

    def release(self, resource_uri: str) -> None:
        with self._lock_obj:
            lock = self._locks.get(resource_uri)
            if lock is not None:
                lock.release()
                del self._locks[resource_uri]
                logger.info("state lock: released %s", resource_uri)

    def is_locked(self, resource_uri: str) -> bool:
        with self._lock_obj:
            lock = self._locks.get(resource_uri)
            return lock is not None and lock.is_active

    def active_locks(self) -> list[ResourceLock]:
        with self._lock_obj:
            return [lock for lock in self._locks.values() if lock.is_active]

    def pre_execution_conflict(self, resource_uris: list[str]) -> list[tuple[str, str]]:
        conflicts: list[tuple[str, str]] = []
        with self._lock_obj:
            for uri in resource_uris:
                lock = self._locks.get(uri)
                if lock is not None and lock.is_active:
                    conflicts.append((uri, CONCURRENT_RESOURCE_CONFLICT))
        return conflicts
