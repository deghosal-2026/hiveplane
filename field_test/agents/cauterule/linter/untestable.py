"""Untestable check — detects directives that cannot be verified via replay."""

from __future__ import annotations

_UNVERIFIABLE_PHRASES = frozenset(
    {
        "think carefully",
        "think step by step",
        "reason about",
        "consider the implications",
        "analyze the situation",
        "understand the context",
        "use your judgment",
        "reflect on",
        "contemplate",
        "ponder",
        "be aware of",
        "remember to",
        "keep in mind",
        # Observed LLM filler with no replay-verifiable outcome (#504).
        "use best judgment",
        "exercise judgment",
        "think it through",
        "weigh the options",
        "consider carefully",
    }
)


def check_untestable(directive: str) -> list[str]:
    """Return warnings if directive contains unverifiable phrases."""
    lower = directive.lower()
    return [f"untestable: '{p}'" for p in _UNVERIFIABLE_PHRASES if p in lower]
