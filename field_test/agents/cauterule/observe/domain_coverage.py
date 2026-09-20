"""Domain-level coverage score — coverage ratio by tag-based domain."""

from __future__ import annotations

from collections import defaultdict

from cauterule.store.manager import StoreManager


def domain_coverage(store: StoreManager) -> dict[str, float]:
    """Return a dict mapping each domain (from rule tags) to its coverage ratio.

    Coverage for a domain is defined as the fraction of active rules in that
    domain that have a hit count > 0.
    """
    rules = store.list_rules()
    active = [r for r in rules if r.status == "active"]
    if not active:
        return {}

    domain_counts: dict[str, list[int]] = defaultdict(list)
    for r in active:
        for tag in r.tags:
            domain_counts[tag].append(r.hit_count)

    covered: dict[str, float] = {}
    for domain, hit_counts in domain_counts.items():
        covered_rules = sum(1 for hc in hit_counts if hc > 0)
        covered[domain] = round(covered_rules / len(hit_counts), 4)

    return covered
