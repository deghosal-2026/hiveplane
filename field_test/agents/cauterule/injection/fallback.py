"""No-match graceful degradation — empty set if no rules match."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def no_match_fallback(task: str) -> list[StandingRule]:
    """Graceful degradation when no rules match.

    Returns an empty list, indicating that no standing rules are applicable
    to *task*.  Callers may use this to skip injection entirely or fall
    back to generic behaviour.

    Args:
        task: The task that had no matching rules (logged for audit).

    Returns:
        Always returns an empty list.
    """
    _ = task
    return []
