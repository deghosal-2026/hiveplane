"""Trigger specificity scoring — penalize over-broad triggers.

Categories:
- specific: names a concrete tool, error, or condition
- moderate: names a domain or general condition
- generic: applies to almost anything
"""

from __future__ import annotations

import re
from typing import Literal

Specificity = Literal["specific", "moderate", "generic"]

# Degenerate trigger patterns — step identifiers, not failure descriptions.
_DEGENERATE_RE = re.compile(r"^step[_\s]*\d+$", re.IGNORECASE)

# A hyphenated/underscored token signals a concrete error code only when it
# looks code-like — it contains a digit or a camelCase seam. Plain English
# hyphenation such as "does-not-work" is a word separator, not an error code
# (#784).
_HYPHEN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+")
_DIGIT_RE = re.compile(r"\d")
_CAMEL_SEAM_RE = re.compile(r"[a-z][A-Z]")

# Concrete error-condition markers (multi-word or hyphenated).
_CONCRETE_MARKERS: frozenset[str] = frozenset(
    {
        "non-fast-forward",
        "non fast forward",
        "merge conflict",
        "permission denied",
        "modulenotfounderror",
        "import error",
        "connection refused",
        "missing peer dependency",
        "exit_code",
        "exit-code",
        "exit code",
    }
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "doing",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "ought",
        "to",
        "of",
        "in",
        "for",
        "on",
        "by",
        "with",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "not",
        "no",
        "nor",
        "as",
        "at",
        "from",
        "it",
        "its",
        "you",
        "your",
        "we",
        "they",
    }
)


def _has_code_like_token(trigger: str) -> bool:
    """Return True if *trigger* holds a code-like hyphenated/underscored token.

    Code-like means the token carries a digit or a camelCase seam; plain
    alphabetic hyphenation is treated as word separation instead (#784).
    """
    for match in _HYPHEN_TOKEN_RE.finditer(trigger):
        token = match.group(0)
        if _DIGIT_RE.search(token) or _CAMEL_SEAM_RE.search(token):
            return True
    return False


def _content_tokens(trigger: str) -> list[str]:
    lower = trigger.lower()
    # Plain hyphens/underscores separate words so "does-not-work" counts the
    # same as "does not work".
    separated = re.sub(r"[-_]+", " ", lower)
    tokens = [t.strip(".,;:!?\"'()[]{}") for t in separated.split()]
    return [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]


def score_specificity(trigger: str) -> Specificity:
    """Return specificity category for *trigger*."""
    lower = trigger.lower()
    tokens = _content_tokens(trigger)

    # Reject degenerate triggers (step identifiers like "step_1").
    if _DEGENERATE_RE.match(lower.strip()):
        return "generic"

    # Strong signal: code-like hyphenated token or concrete error phrase.
    if _has_code_like_token(trigger):
        return "specific"
    for phrase in _CONCRETE_MARKERS:
        if phrase in lower:
            return "specific"

    if len(tokens) >= 4:
        return "specific"
    if len(tokens) == 3:
        return "moderate"
    # 2-token triggers with a concrete tool name are moderate, not generic.
    if len(tokens) == 2 and any(
        t in {"git", "docker", "npm", "pip", "kubectl", "pytest"} for t in tokens
    ):
        return "moderate"
    return "generic"


def is_generic(trigger: str) -> bool:
    """Return True if *trigger* is generic (should be flagged)."""
    return score_specificity(trigger) == "generic"
