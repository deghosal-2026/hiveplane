"""Failure-class coverage — percentage of recurring failure classes with rules."""

from __future__ import annotations

from collections import Counter

from cauterule.models.trajectory import Trajectory
from cauterule.store.manager import StoreManager


def class_coverage(store: StoreManager, trajectories: list[Trajectory]) -> dict[str, float]:
    """Return a dict mapping each recurring failure class to its coverage
    indicator (``1.0`` if covered, ``0.0`` if not).

    A failure class is considered recurring if it appears in at least 2
    failed trajectories.  It is considered covered if any rule's tags or
    taxonomy match the failure class name.
    """
    failure_classes: Counter[str] = Counter()
    for t in trajectories:
        if not t.success and t.failure_class:
            failure_classes[t.failure_class] += 1

    if not failure_classes:
        return {}

    rules = store.list_rules()
    rule_taxonomies = {r.taxonomy for r in rules if r.taxonomy}
    rule_tags: set[str] = set()
    for r in rules:
        rule_tags.update(r.tags)

    covered: dict[str, float] = {}
    for fc, count in failure_classes.most_common():
        if count < 2:
            continue
        is_covered = fc in rule_tags or fc in rule_taxonomies
        covered[fc] = 1.0 if is_covered else 0.0

    return covered
