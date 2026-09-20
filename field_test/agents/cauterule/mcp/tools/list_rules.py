"""MCP tool: ``list_rules`` — browse the rule store with optional filters."""

from __future__ import annotations

from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager


def list_rules(
    status: str | None = None,
    tag: str | None = None,
    store: StoreManager | None = None,
) -> list[StandingRule]:
    """Return rules matching the given *status* and/or *tag* filters.

    Args:
        status: Filter by rule status (``"active"``, ``"retired"``,
            ``"superseded"``). ``None`` means no status filter.
        tag: Filter by tag (case-insensitive substring match).
            ``None`` means no tag filter.
        store: The rule store. Required.

    Returns:
        Filtered list of :class:`StandingRule` instances.
    """
    if store is None:
        return []

    rules = store.list_rules(status=status)

    if tag is not None:
        tag_lower = tag.lower()
        rules = [r for r in rules if any(tag_lower in t.lower() for t in r.tags)]

    return rules
