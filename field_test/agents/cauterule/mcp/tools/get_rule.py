"""MCP tool: ``get_rule`` — retrieve a single rule with full provenance."""

from __future__ import annotations

from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager


def get_rule(rule_id: str, store: StoreManager) -> StandingRule | None:
    """Return the rule identified by *rule_id*, or ``None`` if not found.

    The returned rule includes full provenance metadata (source trajectory,
    extraction info, replay evidence, promotion commit).
    """
    return store.get_rule(rule_id)
