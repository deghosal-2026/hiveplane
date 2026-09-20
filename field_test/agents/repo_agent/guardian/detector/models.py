from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MarkerMatch:
    tool: str
    pattern: str
    line: int
    confidence: Literal["high", "medium"]


@dataclass
class DetectionResult:
    file_path: str
    total_lines: int
    matches: list[MarkerMatch] = field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
