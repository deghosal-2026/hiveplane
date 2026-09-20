from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from ..schema.plan import PlanVersion
from ..types import Finding


class BaseGate(ABC):
    """Base class for a single deterministic gate."""

    name: str = "base"
    author: str | None = None
    rationale: str | None = None
    added_at: datetime | None = None
    stale_at: datetime | None = None
    amend_conditions: str | None = None

    @abstractmethod
    def run(self, plan: PlanVersion) -> list[Finding]:
        raise NotImplementedError

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "author": self.author,
            "rationale": self.rationale,
            "added_at": self.added_at.isoformat() if self.added_at else None,
            "stale_at": self.stale_at.isoformat() if self.stale_at else None,
            "amend_conditions": self.amend_conditions,
            "is_stale": self._is_stale() if self.stale_at else False,
        }

    def _is_stale(self) -> bool:
        if self.stale_at is None:
            return False
        return datetime.now(UTC) > self.stale_at

    def __repr__(self) -> str:
        parts = [self.name]
        if self.author:
            parts.append(f"by={self.author}")
        return f"{self.__class__.__name__}({', '.join(parts)})"
