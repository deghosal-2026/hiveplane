"""Spend-budget enforcement (PRD §2.4, F-06 — M1 deterministic version).

The per-goal ``constraints.budget`` is allowed to cap `revisions`, `calls`,
and `tokens`. Hitting any ceiling escalates rather than spending more. M1
implements the bookkeeping as a simple counter; M3 owns LLM-token
accounting and can hand this module real token counts.

Deterministic: the same plan history produces the same "budget exceeded"
decision every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schema.goal import Budget


@dataclass
class SpendState:
    """Running spend counters for one goal's planning loop."""

    revisions_used: int = 0
    calls_used: int = 0
    tokens_used: int = 0
    exceeded: bool = False
    _hits: list[str] = field(default_factory=list)

    def record_revision(self) -> None:
        """Count one planner revision round."""
        self.revisions_used += 1

    def record_llm_call(self, tokens: int = 0) -> None:
        """Count one LLM provider call, optionally with a token count."""
        self.calls_used += 1
        self.tokens_used += tokens

    def check(self, budget: Budget) -> bool:
        """Evaluate the counters against the goal budget.

        Args:
            budget: The goal's spend ceiling (optional fields = uncapped).

        Returns:
            True once any configured ceiling is exceeded. Idempotent — the
            first breach latches ``exceeded``.
        """
        if self.exceeded:
            return True
        if budget.max_revisions is not None and self.revisions_used > budget.max_revisions:
            self._breach("max_revisions")
        if budget.max_calls is not None and self.calls_used > budget.max_calls:
            self._breach("max_calls")
        if budget.max_tokens is not None and self.tokens_used > budget.max_tokens:
            self._breach("max_tokens")
        return self.exceeded

    def _breach(self, ceiling: str) -> None:
        """Latch the exceeded flag and record which ceiling was breached."""
        self.exceeded = True
        self._hits.append(ceiling)


def budget_exceeded(budget: Budget, state: SpendState) -> bool:
    """Convenience wrapper: did the loop blow its spend budget?

    Args:
        budget: The goal's spend ceiling.
        state: Current spend counters.

    Returns:
        True when any configured ceiling is exceeded.
    """
    return state.check(budget)
