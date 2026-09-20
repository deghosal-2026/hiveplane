from dataclasses import dataclass, field


@dataclass
class Violation:
    rule_name: str
    severity: str  # low, medium, high, critical
    file_path: str
    line: int | None
    pattern_found: str
    explanation: str
    remediation: str
    governance_url: str | None = None


@dataclass
class ReviewResult:
    violations: list[Violation] = field(default_factory=list)
    risk_level: str = "low"
    summary: str = ""
