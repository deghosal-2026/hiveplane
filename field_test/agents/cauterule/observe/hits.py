"""Per-rule hit counter — aggregate hit counts across the rule store."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.rule import StandingRule
from cauterule.serialization.rule_yaml import dump_rule_to_file
from cauterule.store.manager import StoreManager


def get_hit_counts(store: StoreManager) -> dict[str, int]:
    """Return a dict mapping each rule id to its current hit count."""
    return {r.id: r.hit_count for r in store.list_rules()}


def increment_hit_count(
    store: StoreManager,
    rule_id: str,
    timestamp: str | None = None,
) -> StandingRule:
    """Increment hit count for rule and update last_match timestamp.

    Args:
        store: Store manager.
        rule_id: Rule id to increment.
        timestamp: ISO8601 timestamp, defaults to now UTC.

    Returns:
        Updated rule.

    Raises:
        ValueError: If rule not found.
    """
    rule = store.get_rule(rule_id)
    if rule is None:
        msg = f"Rule {rule_id!r} not found"
        raise ValueError(msg)
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = StandingRule(
        id=rule.id,
        when=rule.when,
        do=rule.do,
        confidence=rule.confidence,
        provenance=rule.provenance,
        status=rule.status,
        promoted_at=rule.promoted_at,
        hit_count=rule.hit_count + 1,
        last_match=timestamp,
        tags=rule.tags,
        taxonomy=rule.taxonomy,
        template=rule.template,
        pack=rule.pack,
        retired_at=rule.retired_at,
        retirement_reason=rule.retirement_reason,
        superseded_by=rule.superseded_by,
    )
    dump_rule_to_file(updated, store._rule_path(rule_id))
    return updated


def record_injection_hits(
    store: StoreManager,
    rule_ids: list[str],
    timestamp: str | None = None,
) -> dict[str, int]:
    """Record hits for multiple rules, return updated counts."""
    counts: dict[str, int] = {}
    for rid in rule_ids:
        rule = increment_hit_count(store, rid, timestamp=timestamp)
        counts[rid] = rule.hit_count
    return counts
