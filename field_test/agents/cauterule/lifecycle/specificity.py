"""Stored per-rule specificity scoring (#541).

Produce and persist a 0..1 ``specificity`` score per standing rule — a
combined trigger-breadth + outcome-precision metric.  Over-broad triggers
(e.g. ``"fails"``, ``"error"``, ``"does not work"``) score low and are
surfaced in ``list``/``show``/``report`` and usable by retirement and
promotion policy.

Formula (documented; tuned in field test #653):

    trigger_score(t)   = content-token specificity  (0..1)
    precision(t)       = prevented / (prevented + broke),
                         shrunk toward 0.5 when the outcome sample is small
    breadth_penalty(t) = matched_irrelevant / matched (0 when none)

    specificity = 0.6*trigger_score + 0.4*precision - 0.2*breadth_penalty
                  clamped to [0, 1]

Reference threshold for "broad" is 0.3 (configurable via ``config``).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from cauterule.extraction.specificity import _CONCRETE_MARKERS, _STOPWORDS, _has_code_like_token
from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager

# Reference "broad" threshold (issue #541 acceptance gate default).
BROAD_SPECIFICITY_THRESHOLD = 0.3

# Weight blend for the composite score.
_TRIGGER_WEIGHT = 0.6
_PRECISION_WEIGHT = 0.4
_BREADTH_PENALTY = 0.2

# Prior for the precision shrinkage: with tiny samples the score leans on
# the trigger signal and the outcome term stays near-neutral.
_MIN_OUTCOMES_FOR_CONFIDENCE = 10

_DEGENERATE_RE = re.compile(r"^step[_\s]*\d+$", re.IGNORECASE)

# Concrete tool names give a 2-token trigger meaningful signal.
_TOOL_WORDS = frozenset(
    {"git", "docker", "npm", "pip", "kubectl", "pytest", "ssh", "terraform", "gradle"}
)


def _content_tokens(trigger: str) -> list[str]:
    lower = trigger.lower()
    separated = re.sub(r"[-_]+", " ", lower)
    tokens = [t.strip(".,;:!?\"'()[]{}") for t in separated.split()]
    return [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]


def trigger_score(trigger: str) -> float:
    """Return the 0..1 trigger-breadth score for *trigger*.

    Strong signals (concrete error codes, hyphenated identifiers, concrete
    phrases, >= 4 content tokens, or a tool name among 2 tokens) push the
    score up; short vague triggers score near zero.
    """
    lower = trigger.lower().strip()
    if not lower or _DEGENERATE_RE.match(lower):
        return 0.0

    # Code-like tokens / concrete markers are strong specificity evidence.
    if _has_code_like_token(trigger):
        return 1.0
    if any(phrase in lower for phrase in _CONCRETE_MARKERS):
        return 1.0

    tokens = _content_tokens(trigger)
    if len(tokens) >= 4:
        return 1.0
    if len(tokens) == 3:
        return 0.7
    if len(tokens) == 2 and any(t in _TOOL_WORDS for t in tokens):
        return 0.6
    if len(tokens) == 2:
        return 0.4
    return 0.1


def _precision_term(rule: StandingRule) -> float:
    total = rule.prevented_count + rule.broke_count
    if total == 0:
        return 0.5
    observed = rule.prevented_count / total
    # Shrink toward 0.5 when the sample is small.
    weight = min(total / _MIN_OUTCOMES_FOR_CONFIDENCE, 1.0)
    return 0.5 + (observed - 0.5) * weight


def compute_specificity(rule: StandingRule) -> tuple[float, dict[str, Any]]:
    """Compute the 0..1 specificity score and its input breakdown for *rule*.

    Returns:
        ``(score, inputs)`` where *inputs* records trigger_tokens,
        matched_count, broken_count, and updated_at for audit.
    """
    trigger_exact = trigger_score(rule.when.trigger)
    precision = _precision_term(rule)
    matched = rule.prevented_count + rule.broke_count + rule.neutral_count
    broken_ratio = rule.broke_count / matched if matched else 0.0

    score = (
        _TRIGGER_WEIGHT * trigger_exact
        + _PRECISION_WEIGHT * precision
        - _BREADTH_PENALTY * broken_ratio
    )
    score = max(0.0, min(1.0, score))

    inputs: dict[str, Any] = {
        "trigger_tokens": len(_content_tokens(rule.when.trigger)),
        "trigger_score": round(trigger_exact, 4),
        "precision_term": round(precision, 4),
        "matched_count": matched,
        "broken_count": rule.broke_count,
        "breadth_penalty": round(_BREADTH_PENALTY * broken_ratio, 4),
        "updated_at": rule.last_outcome_at,
    }
    return round(score, 4), inputs


def score_and_store(store: StoreManager, rule_id: str) -> StandingRule:
    """Recompute and persist the specificity score for *rule_id*.

    Args:
        store: Rule store.
        rule_id: Rule to score.

    Returns:
        The updated :class:`StandingRule`.
    """
    rule = store.get_rule(rule_id)
    if rule is None:
        msg = f"Rule {rule_id!r} not found"
        raise ValueError(msg)
    score, inputs = compute_specificity(rule)
    updated = replace(rule, specificity=score, specificity_inputs=inputs)
    store.add_rule(updated)
    return updated


def is_broad(rule: StandingRule, threshold: float = BROAD_SPECIFICITY_THRESHOLD) -> bool:
    """Return True if *rule*'s specificity is below *threshold* (broad)."""
    score, _ = compute_specificity(rule) if rule.specificity is None else (rule.specificity, {})
    return score < threshold


def lowest_specificity(
    rules: list[StandingRule],
    limit: int = 10,
) -> list[tuple[StandingRule, float]]:
    """Return the *limit* rules with the lowest specificity, sorted ascending."""
    scored = [
        (r, r.specificity if r.specificity is not None else compute_specificity(r)[0])
        for r in rules
    ]
    scored.sort(key=lambda pair: pair[1])
    return scored[:limit]
