"""Failure-pattern leaderboard — most prevented, most broken, top gaps."""

from __future__ import annotations

from typing import Any

from cauterule.store.manager import StoreManager


def get_leaderboard(store: StoreManager) -> dict[str, list[dict[str, Any]]]:
    """Return a leaderboard dict with three keys.

    ``"most_prevented"``
        Rules sorted descending by failures-prevented count.
    ``"most_broken"``
        Rules sorted descending by successes-broken count.
    ``"top_gaps"``
        Rules sorted ascending by recall (lowest first).
    """
    rules = store.list_rules()

    prevented: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for r in rules:
        ev = r.provenance.replay_evidence
        if ev is not None:
            fp_count = len(ev.failures_prevented)
            sb_count = len(ev.successes_broken)
            prevented.append({"id": r.id, "count": fp_count})
            broken.append({"id": r.id, "count": sb_count})
            gaps.append({"id": r.id, "recall": ev.recall})

    prevented.sort(key=lambda x: x["count"], reverse=True)
    broken.sort(key=lambda x: x["count"], reverse=True)
    gaps.sort(key=lambda x: x["recall"])

    return {
        "most_prevented": prevented,
        "most_broken": broken,
        "top_gaps": gaps,
    }
