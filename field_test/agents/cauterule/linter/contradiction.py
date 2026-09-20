"""Contradiction check — detects conflicting existing rules."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


# Explicit opposition signals: a same-trigger rule pair only contradicts
# when one side carries one of these cues (or an antonym pair spans the
# two directives). Bare inequality is a refinement, not a contradiction (#504).
# Words match whole-word only ("knot" must not trip "not").
_OPPOSITION_WORDS = frozenset(
    {
        "never",
        "cannot",
        "avoid",
        "disable",
        "disabled",
        "stop",
        "forbid",
        "prohibit",
        "instead",
        "force",
        "wrong",
        "incorrect",
    }
)

_OPPOSITION_PHRASES = frozenset(
    {
        "n't",
        "do not",
        "must not",
        "should not",
        "rather than",
    }
)

_ANTONYM_PAIRS = frozenset(
    {
        frozenset({"push", "pull"}),
        frozenset({"merge", "rebase"}),
        frozenset({"allow", "deny"}),
        frozenset({"enable", "disable"}),
        frozenset({"commit", "revert"}),
        frozenset({"create", "delete"}),
        frozenset({"always", "never"}),
    }
)


def _has_opposition(text: str) -> bool:
    words = set(text.split())
    if words & _OPPOSITION_WORDS:
        return True
    return any(phrase in text for phrase in _OPPOSITION_PHRASES)


def _has_antonym_span(a_tokens: set[str], b_tokens: set[str]) -> bool:
    for pair in _ANTONYM_PAIRS:
        first, second = tuple(pair)
        if (first in a_tokens and second in b_tokens) or (second in a_tokens and first in b_tokens):
            return True
    return False


def check_contradiction(
    candidate_trigger: str, candidate_directive: str, existing_rules: list[StandingRule]
) -> list[str]:
    """Return warnings if candidate contradicts an existing rule.

    Same trigger + identical directive is agreement. Same trigger + a
    directive that extends the existing one (token superset) is a
    refinement. Only an explicit opposition signal or antonym span
    counts as a contradiction.
    """
    ct = _normalize(candidate_trigger)
    cd = _normalize(candidate_directive)
    cd_tokens = set(cd.split())
    for rule in existing_rules:
        if _normalize(rule.when.trigger) != ct:
            continue
        rd = _normalize(rule.do.directive)
        if cd == rd:
            continue
        rd_tokens = set(rd.split())
        # Refinement: one side's tokens extend the other's.
        if cd_tokens >= rd_tokens or rd_tokens >= cd_tokens:
            continue
        if _has_opposition(cd) or _has_opposition(rd) or _has_antonym_span(cd_tokens, rd_tokens):
            return [
                f"contradiction: conflicts with {rule.id} ('{rule.do.directive}' vs '{candidate_directive}')"
            ]
    return []
