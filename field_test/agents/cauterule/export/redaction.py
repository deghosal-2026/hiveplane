"""Redact secrets from exported rule text.

Wraps the redaction engine to provide a convenience function for stripping
secrets from export output before writing to disk.
"""

from __future__ import annotations

from cauterule.redaction.engine import contains_secret, redact_text


def redact_export(text: str) -> str:
    """Strip secrets from *text* and return the redacted result.

    Uses the built-in secret patterns from :mod:`cauterule.redaction.patterns`.
    """
    return redact_text(text)


def sanitize_inline(text: str) -> str:
    """Collapse whitespace/newlines so rule text cannot forge export entries.

    Exporters interpolate rule text inline; a newline would let the text
    terminate the current line and forge a heading or rule entry that agents
    then obey (#797). Collapsing runs of whitespace keeps the value on one line.
    """
    return " ".join(text.split())


__all__ = ["contains_secret", "redact_export", "sanitize_inline"]
