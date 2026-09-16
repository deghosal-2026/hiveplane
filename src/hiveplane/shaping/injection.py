"""Prompt-injection scanning of tool output (T14, D4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    """Scans text for injection patterns; full patterns land in the next task."""

    def scan(self, text: str) -> InjectionScanResult:
        """Return a benign result."""
        return InjectionScanResult(verdict=InjectionVerdict.NONE)
