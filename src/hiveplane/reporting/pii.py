"""PII detection, redaction, and chain-safe audit scrubbing (M57-06, D38).

Scrubbing runs before the audit hash is computed so the chain verifies over the
redacted content and raw PII is never persisted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping

from hiveplane.persistence.audit import ScrubbingAuditLog
from hiveplane.reporting.models import Redaction, ScrubResult

__all__ = ["PII_PATTERNS", "PIIScrubber", "ScrubbingAuditLog"]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_CREDIT_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,15}\d(?!\d)")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]+)?(?:\(\d{3}\)|\d{3})[-.\s]+\d{3}[-.\s]+\d{4}(?!\d)"
)
_IPV4 = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": _EMAIL,
    "ssn": _SSN,
    "credit_card": _CREDIT_CARD,
    "phone": _PHONE,
    "ipv4": _IPV4,
}

_NON_DIGITS = re.compile(r"\D")


def _luhn(digits: str) -> bool:
    """Return True when ``digits`` passes the Luhn checksum."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


class PIIScrubber:
    """Detects and redacts PII, and hashes raw values for audit references."""

    def __init__(
        self,
        patterns: Mapping[str, re.Pattern[str]]
        | Iterable[tuple[str, re.Pattern[str]]]
        | None = None,
        *,
        salt: str = "hiveplane-pii",
        enabled: bool = True,
    ) -> None:
        normalized: Mapping[str, re.Pattern[str]]
        if patterns is None:
            normalized = PII_PATTERNS
        elif isinstance(patterns, Mapping):
            normalized = patterns
        else:
            normalized = dict(patterns)
        self._patterns: dict[str, re.Pattern[str]] = dict(normalized)
        self._salt = salt
        self._enabled = enabled

    def scrub(self, text: str | None) -> ScrubResult:
        """Redact every PII match in ``text`` and count redactions by kind."""
        if text is None:
            return ScrubResult(text="", redactions=[])
        if not self._enabled:
            return ScrubResult(text=text, redactions=[])
        counts: dict[str, int] = {}
        redacted = text
        for kind, pattern in self._patterns.items():
            redacted = self._redact(kind, pattern, redacted, counts)
        redactions = [
            Redaction(kind=kind, count=count) for kind, count in counts.items()
        ]
        return ScrubResult(text=redacted, redactions=redactions)

    def scrub_hash(self, value: str) -> str:
        """Return a salted sha256 hex digest of the raw ``value``."""
        return hashlib.sha256(f"{self._salt}:{value}".encode()).hexdigest()

    @staticmethod
    def _redact(
        kind: str,
        pattern: re.Pattern[str],
        text: str,
        counts: dict[str, int],
    ) -> str:
        def _replace(match: re.Match[str]) -> str:
            if kind == "credit_card":
                digits = _NON_DIGITS.sub("", match.group())
                if not _luhn(digits):
                    return match.group()
            counts[kind] = counts.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"

        return pattern.sub(_replace, text)

