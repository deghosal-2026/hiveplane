"""Store health report — coverage, stale rules, conflicts, etc."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from cauterule.models.rule import StandingRule
from cauterule.serialization.rule_yaml import load_rules_from_dir


def health_report(base_dir: str = "rules") -> dict[str, Any]:
    """Produce a health snapshot of the rule store.

    Args:
        base_dir: Root rule-store directory.

    Returns:
        A dict with keys:
            - ``total_rules``
            - ``by_status``: counts per status
            - ``avg_confidence``
            - ``avg_effectiveness`` (hit_count / total matches heuristic)
            - ``stale_rules``: rules with last_match older than 30 days or never matched
            - ``conflict_count``: number of rules with conflicting triggers
    """
    rules = load_rules_from_dir(base_dir)

    total = len(rules)
    by_status: dict[str, int] = {}
    confidences: list[float] = []
    active_rules: list[StandingRule] = []

    for r in rules:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        confidences.append(r.confidence)
        if r.status == "active":
            active_rules.append(r)

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    total_hits = sum(r.hit_count for r in active_rules)
    avg_effectiveness = total_hits / len(active_rules) if active_rules else 0.0

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)
    stale_rules: list[str] = []
    for r in active_rules:
        if r.last_match is None:
            stale_rules.append(r.id)
            continue
        try:
            if isinstance(r.last_match, str):
                from datetime import datetime as dt_parse

                last = dt_parse.fromisoformat(r.last_match)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if last < cutoff:
                    stale_rules.append(r.id)
        except (ValueError, TypeError):
            stale_rules.append(r.id)

    trigger_map: dict[str, list[str]] = {}
    for r in active_rules:
        trigger_map.setdefault(r.when.trigger, []).append(r.id)
    conflict_count = sum(1 for ids in trigger_map.values() if len(ids) > 1)

    # Near-duplicates: rules whose triggers are similar but not identical
    # (#526).  Uses the replay matcher's alias-aware token overlap so
    # paraphrased triggers ("permission denied" ~ "access denied") are
    # surfaced.
    near_duplicate_pairs: list[dict[str, str]] = []
    for i in range(len(active_rules)):
        for j in range(i + 1, len(active_rules)):
            a, b = active_rules[i], active_rules[j]
            if _normalize_trigger(a.when.trigger) == _normalize_trigger(b.when.trigger):
                continue
            if _trigger_similarity(a.when.trigger, b.when.trigger) >= _NEAR_DUP_THRESHOLD:
                near_duplicate_pairs.append(
                    {
                        "rule_a": a.id,
                        "rule_b": b.id,
                        "trigger_a": a.when.trigger,
                        "trigger_b": b.when.trigger,
                    }
                )

    now_str = datetime.now(UTC).isoformat()

    # Supersession integrity (#544): dangling targets, cycles, orphan middles.
    supersession_issues: list[str] = []
    try:
        from cauterule.lifecycle.supersede import all_issues

        supersession_issues = [i.render() for i in all_issues(rules)]
    except Exception:
        pass

    return {
        "total_rules": total,
        "by_status": by_status,
        "avg_confidence": round(avg_confidence, 4),
        "avg_effectiveness": round(avg_effectiveness, 4),
        "stale_rules": stale_rules,
        "conflict_count": conflict_count,
        "near_duplicate_pairs": near_duplicate_pairs,
        "supersession_issues": supersession_issues,
        "report_time": now_str,
    }


# ------------------------------------------------------------------
# Near-duplicate helpers (#526)
# ------------------------------------------------------------------
_NEAR_DUP_THRESHOLD = 0.70


def _normalize_trigger(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _trigger_similarity(a: str, b: str) -> float:
    """Return the Jaccard similarity of two trigger strings (#526, code-review).

    Reuses the replay matcher's alias map so paraphrased failures the
    matcher conflates (e.g. ``"permission denied"`` ~ ``"access denied"``)
    are surfaced.  Only aliases matched by *both* triggers are expanded so
    one-sided over-matching (e.g. ``"permission denied"`` also matching
    the ``"authentication error"`` alias block) does not pollute the bag.
    Jaccard is used so a strictly subsuming trigger does not score 1.0.
    """
    from cauterule.replay.matcher import _ALIASES

    ta = {t for t in _normalize_trigger(a).split() if len(t) > 1}
    tb = {t for t in _normalize_trigger(b).split() if len(t) > 1}
    if not ta or not tb:
        return 0.0
    norm_a = f" {_normalize_trigger(a)} "
    norm_b = f" {_normalize_trigger(b)} "
    # Expand only aliases that fire on BOTH sides (symmetric expansion).
    extra: set[str] = set()
    for key, phrases in _ALIASES.items():
        hit_a = key in norm_a or any(phrase in norm_a for phrase in phrases)
        hit_b = key in norm_b or any(phrase in norm_b for phrase in phrases)
        if hit_a and hit_b:
            extra.update(w for w in key.split() if len(w) > 1)
            for phrase in phrases:
                extra.update(w for w in phrase.split() if len(w) > 1)
    ta |= extra
    tb |= extra
    return len(ta & tb) / len(ta | tb)
