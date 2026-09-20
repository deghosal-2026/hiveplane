"""Pack safety scoring - per-pack 0-100 score from rule lint signals (#481).

Reuses the safety-adjusted machinery: broad-trigger penalty
(:func:`check_broadness`) and the unsafe-directive scan
(:func:`check_unsafe`). The score gates ``pack install`` / ``pack publish``.
"""

from __future__ import annotations

from typing import Any

from cauterule.linter.specificity import check_broadness
from cauterule.linter.unsafe import check_unsafe

UNSAFE_PENALTY = 40
BROAD_PENALTY = 22
DEFAULT_MIN_SAFETY_SCORE = 70


def score_rule_safety(rule_id: str, trigger: str, directive: str) -> dict[str, Any]:
    """Score one rule 0-100 with penalty reasons."""
    score = 100
    reasons: list[str] = []
    unsafe_hits = check_unsafe(directive or "")
    for hit in unsafe_hits:
        score -= UNSAFE_PENALTY
        reasons.append(f"unsafe directive: {hit}")
    broad_hits = check_broadness(trigger or "")
    for hit in broad_hits:
        score -= BROAD_PENALTY
        reasons.append(f"broad trigger (penalty {BROAD_PENALTY}): {hit}")
    return {"rule": rule_id, "score": max(0, score), "reasons": reasons}


def score_pack_safety(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Score a pack from rule dicts with id/trigger/directive keys.

    Returns {score, per_rule, drags} where drags names the rules pulling
    the score down and why.
    """
    if not rules:
        return {"score": 0, "per_rule": [], "drags": ["pack has no rules"]}
    per_rule = [
        score_rule_safety(
            str(r.get("id", "?")),
            str(r.get("trigger", "")),
            str(r.get("directive", "")),
        )
        for r in rules
    ]
    score = round(sum(p["score"] for p in per_rule) / len(per_rule))
    drags = [f"{p['rule']}: {'; '.join(p['reasons'])}" for p in per_rule if p["reasons"]]
    return {"score": score, "per_rule": per_rule, "drags": drags}
