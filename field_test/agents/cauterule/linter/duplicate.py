"""Duplicate check — detects semantically identical existing rules."""

from __future__ import annotations

from cauterule.models.rule import StandingRule

# Overlap-coefficient threshold for near-duplicates (#504): |A∩B| / min(|A|,|B|).
_NEAR_DUPLICATE_THRESHOLD = 0.6

# Failure-mode words.  Two triggers that each carry a *different* failure mode
# (e.g. "hangs" vs "fails") describe separate conditions and must not collapse
# into a near-duplicate even when they share the tool prefix and directive
# (#783).
_FAILURE_MODE_TOKENS: frozenset[str] = frozenset(
    {
        "fail",
        "fails",
        "failed",
        "failing",
        "failure",
        "failures",
        "hang",
        "hangs",
        "hanging",
        "hung",
        "error",
        "errors",
        "errored",
        "crash",
        "crashes",
        "crashed",
        "crashing",
        "timeout",
        "timeouts",
        "timed",
        "stuck",
        "freeze",
        "freezes",
        "frozen",
        "broken",
        "breaks",
        "broke",
        "rejected",
        "rejects",
        "denied",
        "refused",
        "missing",
        "notfound",
        "unresponsive",
    }
)


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _tokens(s: str) -> set[str]:
    return set(_normalize(s).split())


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _distinct_failure_modes(a: set[str], b: set[str]) -> bool:
    """Return True if *a* and *b* name different failure modes.

    A token unique to each side that is a failure-mode word means the
    triggers describe distinct conditions; treating them as paraphrases
    would block promotion of a genuinely different rule (#783).
    """
    return bool((a - b) & _FAILURE_MODE_TOKENS) and bool((b - a) & _FAILURE_MODE_TOKENS)


def check_duplicate(
    candidate_trigger: str, candidate_directive: str, existing_rules: list[StandingRule]
) -> list[str]:
    """Return warnings if a rule with same trigger+directive exists.

    Exact normalized matches warn as duplicates; paraphrases with high
    token overlap on BOTH trigger and directive warn as near-duplicates,
    unless the triggers name distinct failure modes.
    """
    ct = _normalize(candidate_trigger)
    cd = _normalize(candidate_directive)
    ct_tokens = _tokens(candidate_trigger)
    cd_tokens = _tokens(candidate_directive)
    for rule in existing_rules:
        if _normalize(rule.when.trigger) == ct and _normalize(rule.do.directive) == cd:
            return [f"duplicate: matches existing rule {rule.id}"]
        et_tokens = _tokens(rule.when.trigger)
        if (
            _overlap(ct_tokens, et_tokens) >= _NEAR_DUPLICATE_THRESHOLD
            and _overlap(cd_tokens, _tokens(rule.do.directive)) >= _NEAR_DUPLICATE_THRESHOLD
            and not _distinct_failure_modes(ct_tokens, et_tokens)
        ):
            return [f"near-duplicate: paraphrases existing rule {rule.id}"]
    return []
