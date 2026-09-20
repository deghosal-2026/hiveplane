"""Rule coverage score — weighted blend of coverage, precision, staleness."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.store.manager import StoreManager


def compute_coverage_score(store: StoreManager) -> float:
    """Compute a compound coverage score in ``[0.0, 1.0]``.

    Weighted blend (40% coverage%, 40% precision%, 20% non-stale%)
    across all active rules.
    """
    rules = store.list_rules()
    active = [r for r in rules if r.status == "active"]
    if not active:
        return 0.0

    coverage_pct = sum(1 for r in active if r.hit_count > 0) / len(active)

    precisions = [
        r.provenance.replay_evidence.precision
        for r in active
        if r.provenance.replay_evidence is not None
    ]
    precision_pct = sum(precisions) / len(precisions) if precisions else 0.0

    now = datetime.now(UTC)
    non_stale_pct = sum(
        1
        for r in active
        if r.last_match is not None
        and (now - datetime.fromisoformat(r.last_match).replace(tzinfo=UTC)).days < 30
    ) / len(active)

    score = 0.4 * coverage_pct + 0.4 * precision_pct + 0.2 * non_stale_pct
    return round(score, 4)
