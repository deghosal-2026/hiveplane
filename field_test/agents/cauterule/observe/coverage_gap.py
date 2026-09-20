"""Coverage gap detector — domains with repeated failures but no coverage."""

from __future__ import annotations

from collections import Counter
from typing import Any

from cauterule.models.trajectory import Trajectory
from cauterule.store.manager import StoreManager


def find_coverage_gaps(store: StoreManager, trajectories: list[Trajectory]) -> list[dict[str, Any]]:
    """Return domains that have repeated failures but no matching rule coverage.

    A domain is considered a gap if it appears in at least 2 failed trajectories
    and none of the tags in the active rule set overlap with the domain name.
    """
    rules = store.list_rules()
    rule_tags: set[str] = set()
    for r in rules:
        rule_tags.update(r.tags)

    domain_failures: Counter[str] = Counter()
    for t in trajectories:
        if not t.success and t.domain:
            domain_failures[t.domain] += 1

    gaps: list[dict[str, Any]] = []
    for domain, count in domain_failures.most_common():
        if count >= 2 and domain not in rule_tags:
            gaps.append({"domain": domain, "failure_count": count})

    return gaps
