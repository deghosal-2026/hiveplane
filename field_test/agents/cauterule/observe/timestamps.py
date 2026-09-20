"""Last-match timestamp updates for standing rules."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.rule import StandingRule
from cauterule.serialization.rule_yaml import dump_rule_to_file
from cauterule.store.manager import StoreManager


def update_last_match(store: StoreManager, rule_id: str, timestamp: str | None = None) -> None:
    """Update *rule_id*'s ``last_match`` field in-place.

    Args:
        store: Store manager holding the rule.
        rule_id: Id of the rule to update.
        timestamp: ISO-8601 timestamp.  Defaults to ``datetime.utcnow()``.

    Raises:
        ValueError: If *rule_id* does not exist.
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
        hit_count=rule.hit_count,
        last_match=timestamp,
        tags=rule.tags,
        taxonomy=rule.taxonomy,
        template=rule.template,
        pack=rule.pack,
    )
    dump_rule_to_file(updated, store._rule_path(rule_id))
