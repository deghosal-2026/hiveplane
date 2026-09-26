"""Context-window budget: the fourth runtime budget (M41-01, M41-02).

Enforced live from real provider token counts. A breach pauses the run cleanly
(handled by the execution service) and records the accounting at breach.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ContextAccount(BaseModel):
    """Per-step context accounting for one model call in a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    step: int = Field(ge=0)
    model_identity: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    context_total: int = Field(ge=0)
    recorded_at: AwareDatetime


class ContextBreach(BaseModel):
    """The accounting recorded when a run exceeds its context budget."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    used: int = Field(ge=0)
    limit: int = Field(ge=0)
    step: int = Field(ge=0)
    model_identity: str
    rule_id: str = "guard.context"
    reason: str = "context budget exceeded"


class ContextBudgetGuard:
    """Maintains a running context total per run and checks the ceiling."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._accounts: dict[str, list[ContextAccount]] = defaultdict(list)
        self._totals: dict[str, int] = {}

    def record(
        self,
        run_id: str,
        *,
        step: int,
        model_identity: str,
        input_tokens: int,
        output_tokens: int,
    ) -> ContextAccount:
        """Record one model call's tokens and return the running account."""
        total = self._totals.get(run_id, 0) + input_tokens + output_tokens
        self._totals[run_id] = total
        account = ContextAccount(
            run_id=run_id,
            step=step,
            model_identity=model_identity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_total=total,
            recorded_at=self._clock(),
        )
        self._accounts[run_id].append(account)
        return account

    def total(self, run_id: str) -> int:
        """Return the accumulated context tokens for a run."""
        return self._totals.get(run_id, 0)

    def accounts(self, run_id: str) -> list[ContextAccount]:
        """Return the per-step accounts for a run, in order."""
        return list(self._accounts.get(run_id, []))

    def check(
        self, run_id: str, *, limit: int, warn_at: float = 0.8
    ) -> ContextBreach | None:
        """Return a breach when the run's context total exceeds ``limit``."""
        used = self.total(run_id)
        if used <= limit:
            return None
        accounts = self._accounts.get(run_id, [])
        last = accounts[-1] if accounts else None
        return ContextBreach(
            run_id=run_id,
            used=used,
            limit=limit,
            step=last.step if last else 0,
            model_identity=last.model_identity if last else "unspecified",
        )

    def is_warning(self, run_id: str, *, limit: int, warn_at: float = 0.8) -> bool:
        """Return True when the run is at/over the warning threshold but within limit."""
        used = self.total(run_id)
        return limit > 0 and used >= limit * warn_at and used <= limit
