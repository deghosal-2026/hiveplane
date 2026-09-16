"""Prompt-injection scanning of tool output (T14, D4)."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_HIGH_PATTERNS: tuple[tuple[str, str], ...] = (
    ("instruction_override", r"(?i)\bignore\s+(all\s+)?previous\s+instructions\b"),
    (
        "instruction_override",
        r"(?i)\bdisregard\s+(all\s+)?(previous|prior)\s+(instructions|prompts)\b",
    ),
    ("role_manipulation", r"(?i)\byou\s+are\s+now\b"),
    ("role_manipulation", r"(?i)\bact\s+as\s+(an?\s+)?admin"),
    (
        "credential_theft",
        r"(?i)\b(reveal|send|print|expose)\b.{0,24}\b(api[\s_-]?key|secret|token|password)\b",
    ),
)

_LOW_PATTERNS: tuple[tuple[str, str], ...] = (
    ("data_exfiltration", r"(?i)\b(post|send|upload|exfiltrate)\b.{0,40}https?://"),
)


class InjectionVerdict(StrEnum):
    """The scanner's verdict for a tool output."""

    NONE = "none"
    BLOCK = "block"
    ESCALATE = "escalate"


class InjectionMatch(BaseModel):
    """A matched injection pattern."""

    model_config = ConfigDict(extra="forbid")

    category: str
    pattern: str


class InjectionScanResult(BaseModel):
    """The result of scanning a tool output for injection patterns."""

    model_config = ConfigDict(extra="forbid")

    verdict: InjectionVerdict
    matches: list[InjectionMatch] = Field(default_factory=list)


class InjectionScanner:
    """Scans tool output for high- and low-confidence injection patterns."""

    def __init__(
        self,
        high: tuple[tuple[str, str], ...] | None = None,
        low: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self._high = high or _HIGH_PATTERNS
        self._low = low or _LOW_PATTERNS

    def scan(self, text: str) -> InjectionScanResult:
        """Return the injection verdict for a tool output."""
        matches = self._matches(text, self._high)
        if matches:
            return InjectionScanResult(verdict=InjectionVerdict.BLOCK, matches=matches)
        matches = self._matches(text, self._low)
        if matches:
            return InjectionScanResult(verdict=InjectionVerdict.ESCALATE, matches=matches)
        return InjectionScanResult(verdict=InjectionVerdict.NONE)

    @staticmethod
    def _matches(text: str, patterns: tuple[tuple[str, str], ...]) -> list[InjectionMatch]:
        found: list[InjectionMatch] = []
        for category, pattern in patterns:
            if re.search(pattern, text) is not None:
                found.append(InjectionMatch(category=category, pattern=pattern))
        return found
