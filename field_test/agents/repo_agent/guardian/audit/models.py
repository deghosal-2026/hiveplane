import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from guardian.detector.models import DetectionResult, MarkerMatch
from guardian.reviewer.models import Violation


@dataclass
class AuditEntry:
    version: int = 1
    timestamp: str = ""
    event_id: str = ""
    repo: str = ""
    pr_number: int | None = None
    commit_sha: str | None = None
    action: Literal[
        "analysis", "comment_posted", "label_applied",
        "check_run_created", "feedback_received", "error"
    ] = "analysis"
    detection_results: list | None = None
    violations: list | None = None
    risk_level: str | None = None
    feedback: Literal["confirmed", "disputed"] | None = None
    duration_ms: int = 0
    error: str | None = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.event_id:
            self.event_id = str(uuid4())
        if self.violations is not None:
            self.violations = [
                v if isinstance(v, Violation) else _dict_to_violation(v)
                for v in self.violations
            ]
        if self.detection_results is not None:
            self.detection_results = [
                d if isinstance(d, DetectionResult) else _dict_to_detection(d)
                for d in self.detection_results
            ]

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, default=str, ensure_ascii=False)


def _dict_to_violation(d: dict) -> Violation:
    return Violation(
        rule_name=d.get("rule_name", ""),
        severity=d.get("severity", "low"),
        file_path=d.get("file_path", ""),
        line=d.get("line"),
        pattern_found=d.get("pattern_found", ""),
        explanation=d.get("explanation", ""),
        remediation=d.get("remediation", ""),
        governance_url=d.get("governance_url"),
    )


def _dict_to_detection(d: dict) -> DetectionResult:
    matches_data = d.get("matches", [])
    matches = [
        m if isinstance(m, MarkerMatch) else MarkerMatch(
            tool=m.get("tool", ""),
            pattern=m.get("pattern", ""),
            line=m.get("line", 0),
            confidence=m.get("confidence", "medium"),
        )
        for m in matches_data
    ]
    return DetectionResult(
        file_path=d.get("file_path", ""),
        total_lines=d.get("total_lines", 0),
        matches=matches,
        confidence=d.get("confidence", "low"),
    )
