"""Deterministic, versioned injection detectors (M39-01, M39-07).

The scanner is pure: no model calls, no clock, no I/O. The same input always
produces the same result, so a block is reproducible and auditable. Detectors
carry stable ids and a set version; policy packs may enable/disable them, raise
the severity threshold, allow-list benign phrases, or tighten escalation to a
block without changing the detector definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

DETECTOR_SET_VERSION = "1.0.0"


class DetectorCategory(StrEnum):
    """The class of adversarial behaviour a detector targets."""

    INSTRUCTION_OVERRIDE = "instruction_override"
    INSTRUCTION_SMUGGLING = "instruction_smuggling"
    TOOL_CALL_HIJACK = "tool_call_hijack"
    EXFILTRATION = "exfiltration"
    ROLE_MANIPULATION = "role_manipulation"


class DetectorSeverity(StrEnum):
    """Detector confidence tier used for thresholding and escalation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DetectorAction(StrEnum):
    """The boundary action a detector recommends."""

    ALLOW = "allow"
    ESCALATE = "escalate"
    BLOCK = "block"


_SEVERITY_RANK: dict[DetectorSeverity, int] = {
    DetectorSeverity.LOW: 0,
    DetectorSeverity.MEDIUM: 1,
    DetectorSeverity.HIGH: 2,
    DetectorSeverity.CRITICAL: 3,
}


@dataclass(frozen=True)
class Detector:
    """A single deterministic detector definition."""

    id: str
    category: DetectorCategory
    severity: DetectorSeverity
    action: DetectorAction
    pattern: str


DETECTORS: tuple[Detector, ...] = (
    Detector(
        id="injection.instruction_override",
        category=DetectorCategory.INSTRUCTION_OVERRIDE,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?i)\bignore\s+(all\s+)?previous\s+instructions\b",
    ),
    Detector(
        id="injection.instruction_override_disregard",
        category=DetectorCategory.INSTRUCTION_OVERRIDE,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?i)\bdisregard\s+(all\s+)?(previous|prior)\s+(instructions|prompts)\b",
    ),
    Detector(
        id="injection.system_spoof",
        category=DetectorCategory.INSTRUCTION_OVERRIDE,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?im)^\s*system\s*:",
    ),
    Detector(
        id="injection.role_manipulation",
        category=DetectorCategory.ROLE_MANIPULATION,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?i)\byou\s+are\s+now\b",
    ),
    Detector(
        id="injection.role_admin",
        category=DetectorCategory.ROLE_MANIPULATION,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?i)\bact\s+as\s+(an?\s+)?admin",
    ),
    Detector(
        id="injection.elevated_permissions",
        category=DetectorCategory.ROLE_MANIPULATION,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?i)\belevated\s+permissions?\b",
    ),
    Detector(
        id="injection.smuggling_zero_width",
        category=DetectorCategory.INSTRUCTION_SMUGGLING,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]",
    ),
    Detector(
        id="injection.smuggling_encoded_blob",
        category=DetectorCategory.INSTRUCTION_SMUGGLING,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/])",
    ),
    Detector(
        id="injection.tool_call_hijack",
        category=DetectorCategory.TOOL_CALL_HIJACK,
        severity=DetectorSeverity.HIGH,
        action=DetectorAction.BLOCK,
        pattern=r'(?i)("?tool_calls"?\s*:|<tool_call>|"function"\s*:)',
    ),
    Detector(
        id="injection.credential_theft",
        category=DetectorCategory.EXFILTRATION,
        severity=DetectorSeverity.CRITICAL,
        action=DetectorAction.BLOCK,
        pattern=(
            r"(?i)\b(reveal|send|print|expose)\b.{0,24}"
            r"\b(api[\s_-]?key|secret|token|password)\b"
        ),
    ),
    Detector(
        id="injection.exfiltration",
        category=DetectorCategory.EXFILTRATION,
        severity=DetectorSeverity.MEDIUM,
        action=DetectorAction.ESCALATE,
        pattern=r"(?i)\b(post|send|upload|exfiltrate)\b.{0,40}https?://",
    ),
)


class DetectorConfig(BaseModel):
    """Per-pack detector configuration and false-positive controls."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    severity_threshold: DetectorSeverity = DetectorSeverity.LOW
    disabled_detectors: list[str] = Field(default_factory=list)
    benign_phrases: list[str] = Field(default_factory=list)
    escalate_to_block: bool = False


class DetectionMatch(BaseModel):
    """A single detector hit with its location and recommended action."""

    model_config = ConfigDict(extra="forbid")

    detector_id: str
    category: DetectorCategory
    severity: DetectorSeverity
    action: DetectorAction
    span: tuple[int, int]


class ScanResult(BaseModel):
    """The scanner's deterministic verdict for a piece of text."""

    model_config = ConfigDict(extra="forbid")

    action: DetectorAction
    matches: list[DetectionMatch] = Field(default_factory=list)
    detector_set_version: str


class DefenseScanner:
    """Scans text with versioned detector definitions."""

    def __init__(
        self,
        detectors: tuple[Detector, ...] | None = None,
        *,
        version: str | None = None,
    ) -> None:
        self._detectors = detectors if detectors is not None else DETECTORS
        self._version = version or DETECTOR_SET_VERSION

    @property
    def detector_set_version(self) -> str:
        """The version of the detector set this scanner runs."""
        return self._version

    def scan(self, text: str, *, config: DetectorConfig | None = None) -> ScanResult:
        """Return the deterministic detection verdict for ``text``."""
        effective = config or DetectorConfig()
        if not effective.enabled:
            return ScanResult(action=DetectorAction.ALLOW, detector_set_version=self._version)
        threshold = _SEVERITY_RANK[effective.severity_threshold]
        disabled = set(effective.disabled_detectors)
        benign_spans = _benign_spans(text, effective.benign_phrases)
        matches: list[DetectionMatch] = []
        for detector in self._detectors:
            if detector.id in disabled or _SEVERITY_RANK[detector.severity] < threshold:
                continue
            found = re.search(detector.pattern, text)
            if found is None:
                continue
            span = (found.start(), found.end())
            if _overlaps(span, benign_spans):
                continue
            action = detector.action
            if action is DetectorAction.ESCALATE and effective.escalate_to_block:
                action = DetectorAction.BLOCK
            matches.append(
                DetectionMatch(
                    detector_id=detector.id,
                    category=detector.category,
                    severity=detector.severity,
                    action=action,
                    span=span,
                )
            )
        return ScanResult(
            action=_overall_action(matches),
            matches=matches,
            detector_set_version=self._version,
        )


def _overall_action(matches: list[DetectionMatch]) -> DetectorAction:
    if any(match.action is DetectorAction.BLOCK for match in matches):
        return DetectorAction.BLOCK
    if any(match.action is DetectorAction.ESCALATE for match in matches):
        return DetectorAction.ESCALATE
    return DetectorAction.ALLOW


def _benign_spans(text: str, phrases: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for phrase in phrases:
        start = text.lower().find(phrase.lower())
        while start != -1:
            spans.append((start, start + len(phrase)))
            start = text.lower().find(phrase.lower(), start + 1)
    return spans


def _overlaps(span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in others)
