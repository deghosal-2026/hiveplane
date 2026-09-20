"""Vagueness check — detects generic triggers/directives."""

from __future__ import annotations

_VAGUE_PHRASES = frozenset(
    {
        "be careful",
        "be more careful",
        "pay attention",
        "be mindful",
        "use common sense",
        "be reasonable",
        "be nice",
        "be good",
        "do better",
        "try harder",
        "be professional",
        "be smart",
        "be gentle",
        "be safe",
        "don't mess up",
        "don't break things",
        # Observed LLM filler that says nothing actionable (#504).
        "do the right thing",
        "handle it properly",
        "take appropriate action",
        "follow best practices",
        "ensure correctness",
        "make sure it works",
        "fix accordingly",
        "deal with it appropriately",
        "handle accordingly",
    }
)


def check_vagueness(text: str) -> list[str]:
    """Return warnings if *text* contains vague phrases."""
    lower = text.lower()
    return [f"vague: '{p}'" for p in _VAGUE_PHRASES if p in lower]
