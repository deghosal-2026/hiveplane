"""Coverage frontier — identifies the most valuable domain to target next."""

from __future__ import annotations

from collections import Counter

from cauterule.models.trajectory import Trajectory
from cauterule.store.manager import StoreManager


def suggest_next_frontier(store: StoreManager, trajectories: list[Trajectory]) -> str:
    """Return a human-readable suggestion for the next domain to target.

    Identifies the domain with the most repeated uncovered failures.
    """
    rules = store.list_rules()
    rule_tags: set[str] = set()
    for r in rules:
        rule_tags.update(r.tags)

    domain_failures: Counter[str] = Counter()
    for t in trajectories:
        if not t.success and t.domain:
            domain_failures[t.domain] += 1

    uncovered = [(d, c) for d, c in domain_failures.most_common() if d not in rule_tags]

    if not uncovered:
        return "All domains are covered. Consider deepening existing coverage."

    target_domain, count = uncovered[0]
    return f"Expand coverage in domain '{target_domain}' ({count} uncovered failures)"
