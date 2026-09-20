"""Redaction package."""

from cauterule.redaction.config import get_all_patterns, get_custom_patterns
from cauterule.redaction.engine import contains_secret, redact_text, redact_trajectory
from cauterule.redaction.flag import is_redacted, mark_redacted
from cauterule.redaction.patterns import BUILTIN_PATTERNS, get_builtin_patterns

__all__ = [
    "BUILTIN_PATTERNS",
    "contains_secret",
    "get_all_patterns",
    "get_builtin_patterns",
    "get_custom_patterns",
    "is_redacted",
    "mark_redacted",
    "redact_text",
    "redact_trajectory",
]
