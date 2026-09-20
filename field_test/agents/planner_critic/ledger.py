from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from planner_critic.reason_codes import (
    PRECONDITION_DROPPED_FROM_COMPACTION,
    PRECONDITION_REDUNDANTLY_RE_INJECTED,
)
from planner_critic.schema.plan import PlanVersion

logger = logging.getLogger(__name__)


@dataclass
class LedgerEntry:
    satisfied: bool = False
    satisfied_by: str | None = None
    satisfied_at: datetime | None = None
    verified_by: str | None = None


class PreconditionLedger:
    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}
        self._diagnostics: list[dict[str, Any]] = []

    def mark_satisfied(self, key: str, task_id: str, verified_by: str | None = None) -> None:
        now = datetime.now(UTC)
        self._entries[key] = LedgerEntry(
            satisfied=True,
            satisfied_by=task_id,
            satisfied_at=now,
            verified_by=verified_by,
        )
        logger.info("ledger: marked %r as satisfied by task %s", key, task_id)

    def is_satisfied(self, key: str) -> bool:
        entry = self._entries.get(key)
        return entry is not None and entry.satisfied

    def get_entry(self, key: str) -> LedgerEntry | None:
        return self._entries.get(key)

    def process_plan(self, plan: PlanVersion) -> None:
        self._diagnostics.clear()
        seen_keys: set[str] = set()
        for task in plan.tasks:
            for prec in task.preconditions:
                fact = prec.fact or prec.description
                seen_keys.add(fact)
                if self.is_satisfied(fact):
                    self._diagnostics.append(
                        {
                            "type": PRECONDITION_REDUNDANTLY_RE_INJECTED,
                            "task_id": task.id,
                            "key": fact,
                            "message": (
                                f"Task {task.id} re-injects precondition {fact!r} "
                                "which is already satisfied in the ledger"
                            ),
                        }
                    )
                else:
                    self._diagnostics.append(
                        {"type": "unverified", "task_id": task.id, "key": fact}
                    )
        known_keys = set(self._entries.keys())
        dropped = known_keys - seen_keys
        for key in dropped:
            self._diagnostics.append(
                {
                    "type": PRECONDITION_DROPPED_FROM_COMPACTION,
                    "task_id": None,
                    "key": key,
                    "message": (
                        f"Precondition {key!r} was satisfied in the ledger "
                        "but is absent from this revision — compaction likely"
                    ),
                }
            )

    def diagnostics(self) -> list[dict[str, Any]]:
        return list(self._diagnostics)

    def clear_diagnostics(self) -> None:
        self._diagnostics.clear()

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            k: {
                "satisfied": v.satisfied,
                "satisfied_by": v.satisfied_by,
                "satisfied_at": v.satisfied_at.isoformat() if v.satisfied_at else None,
                "verified_by": v.verified_by,
            }
            for k, v in self._entries.items()
        }
