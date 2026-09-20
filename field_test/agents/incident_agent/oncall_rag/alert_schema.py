from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Alert:
    """
    Schema for inbound alerts placed in alerts/inbox/.

    Fields:
      alert_id: Unique identifier for this alert.
      title: Short summary (e.g. "Pod CrashLoopBackOff").
      message: Human-readable description with context.
      service: Source system (e.g. "kubernetes", "grafana").
      severity: One of "critical", "warning", "info".
      timestamp: When the alert fired (ISO 8601).
      labels: Optional key/value metadata (namespace, pod, host, etc.).
    """
    alert_id: str
    title: str
    message: str
    service: str = "unknown"
    severity: str = "warning"
    timestamp: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class AlertResult:
    """
    Schema for processed results written to alerts/outbox/.

    Fields:
      alert_id: Matches the inbound alert_id.
      title: Copied from inbound alert.
      processed_at: When processing completed (ISO 8601).
      answer: LLM-generated answer (or fallback text).
      sources: List of source citations (file:line-line).
      has_runbook: True if any sources were found.
    """
    alert_id: str
    title: str
    processed_at: str = ""
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    has_runbook: bool = False

    def __post_init__(self) -> None:
        if not self.processed_at:
            self.processed_at = datetime.now(timezone.utc).isoformat()
