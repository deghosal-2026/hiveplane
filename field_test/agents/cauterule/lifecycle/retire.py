"""Automated retirement policy — stale + harmful rules (#543).

A policy engine that flags (and, opt-in, applies) retirement candidates:

- **harmful**: ``broke > prevented`` over a trailing window, with minimum
  evidence (never retire a new 1-for-1 rule).
- **stale**: no outcomes in ``stale_days`` (or zero hits since promotion)
  AND specificity below ``stale_specificity`` — both a rule is stale *and*
  vague to justify removal.

Dry-run by default: the engine computes candidates; mutation only happens
via :func:`apply_policy` (used by ``cauterule audit --apply``). Retired
rules stay on disk (recoverable via rollback/promotion paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cauterule.lifecycle.specificity import BROAD_SPECIFICITY_THRESHOLD
from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager


@dataclass(frozen=True)
class RetirementPolicy:
    """Tunable parameters for the retirement policy."""

    harmful_window: int = 20  # trailing outcomes examined
    harmful_ratio: float = 0.5  # broke/(broke+prevented) above this → candidate
    stale_days: int = 90  # no hits/outcomes in this window → candidate
    stale_specificity: float = BROAD_SPECIFICITY_THRESHOLD  # + spec below this → candidate
    min_evidence: int = 5  # never retire on fewer than N outcomes
    dry_run: bool = True  # default: report, don't mutate


@dataclass(frozen=True)
class RetirementCandidate:
    """A rule flagged for retirement by the policy."""

    rule_id: str
    reason: str
    evidence: dict[str, Any]

    def render(self) -> str:
        """Return a human-readable one-line summary."""
        return (
            f"{self.rule_id} — {self.reason} "
            f"(prevented={self.evidence.get('prevented', 0)}, "
            f"broke={self.evidence.get('broke', 0)}, "
            f"neutral={self.evidence.get('neutral', 0)}, "
            f"last_outcome_at={self.evidence.get('last_outcome_at')})"
        )


def _is_harmful(rule: StandingRule, policy: RetirementPolicy) -> bool:
    # Compute the ratio over the trailing outcome window (from the trend)
    # falling back to lifetime counters when the trend is shorter than the
    # window (code-review).
    window = policy.harmful_window
    trend = rule.outcome_trend
    if len(trend) >= window and any(v == -1 or v == 1 for v in trend[-window:]):
        prevented = sum(1 for v in trend[-window:] if v == 1)
        broke = sum(1 for v in trend[-window:] if v == -1)
    else:
        prevented = rule.prevented_count
        broke = rule.broke_count
    total = prevented + broke
    if total < policy.min_evidence:
        return False
    ratio = broke / total
    return ratio > policy.harmful_ratio


def _is_stale(rule: StandingRule, policy: RetirementPolicy) -> bool:
    # Stale requires BOTH age AND low specificity.
    from cauterule.lifecycle.specificity import compute_specificity

    spec = rule.specificity
    if spec is None:
        spec, _ = compute_specificity(rule)
    if spec >= policy.stale_specificity:
        return False

    if rule.hit_count == 0 and rule.last_match is None:
        return True  # never fired since promotion, and vague
    last = rule.last_outcome_at or rule.last_match
    if not last:
        return True
    try:
        ts = datetime.fromisoformat(last)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) > timedelta(days=policy.stale_days)


def evaluate(
    rules: list[StandingRule],
    policy: RetirementPolicy | None = None,
) -> list[RetirementCandidate]:
    """Return retirement candidates for *rules* under *policy*.

    Args:
        rules: The rule store's rules (any status; only ``active`` considered).
        policy: Policy parameters; defaults to :class:`RetirementPolicy`.

    Returns:
        Candidates, each with a reason and evidence dict.
    """
    p = policy or RetirementPolicy()
    candidates: list[RetirementCandidate] = []
    for rule in rules:
        if rule.status != "active":
            continue
        if rule.superseded_by is not None:
            continue  # never retire a mid-chain link (#544)
        if _is_harmful(rule, p):
            candidates.append(
                RetirementCandidate(
                    rule_id=rule.id,
                    reason="auto:harmful",
                    evidence={
                        "prevented": rule.prevented_count,
                        "broke": rule.broke_count,
                        "neutral": rule.neutral_count,
                        "last_outcome_at": rule.last_outcome_at,
                        "ratio": round(
                            rule.broke_count / (rule.prevented_count + rule.broke_count), 3
                        ),
                    },
                )
            )
        elif _is_stale(rule, p):
            candidates.append(
                RetirementCandidate(
                    rule_id=rule.id,
                    reason="auto:stale",
                    evidence={
                        "prevented": rule.prevented_count,
                        "broke": rule.broke_count,
                        "hit_count": rule.hit_count,
                        "last_match": rule.last_match,
                        "last_outcome_at": rule.last_outcome_at,
                    },
                )
            )
    return candidates


def apply_candidates(
    store: StoreManager,
    candidates: list[RetirementCandidate],
    *,
    audit_log: bool = True,
) -> list[str]:
    """Retire each *candidate* in the store; return retired rule ids.

    Args:
        store: Rule store.
        candidates: Candidates from :func:`evaluate`.
        audit_log: When True, append an audit line to
            ``<base_dir>/outcomes/retirements.jsonl``.

    Returns:
        Ids of rules actually retired (skips any that are no longer active).
    """
    retired_ids: list[str] = []
    for cand in candidates:
        rule = store.get_rule(cand.rule_id)
        if rule is None or rule.status != "active":
            continue
        store.retire_rule(cand.rule_id, reason=cand.reason)
        retired_ids.append(cand.rule_id)
        if audit_log:
            _log_retirement(store, cand)
    return retired_ids


def _log_retirement(store: StoreManager, cand: RetirementCandidate) -> None:
    import json

    path = store.base_dir / "outcomes" / "retirements.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "rule_id": cand.rule_id,
        "reason": cand.reason,
        "evidence": cand.evidence,
        "retired_at": datetime.now(UTC).isoformat(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
