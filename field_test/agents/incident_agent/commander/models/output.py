"""Output models: IncidentResult, SessionMeta, LLMCall."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .state import (
    CostReport,
    DeployCorrelation,
    Postmortem,
    RemediationSuggestion,
    StakeholderUpdate,
    TimelineEvent,
)


class SessionMeta(BaseModel):
    """Session metadata written to output directory."""

    thread_id: str  # LangGraph checkpoint key, also used as session directory name
    models_used: list[str] = Field(default_factory=list)  # distinct LLM models invoked
    total_cost_usd: float = 0.0  # aggregated across all LLM calls
    total_tokens: int = 0  # input + output tokens across all calls
    started_at: datetime | None = None  # wall-clock session start
    ended_at: datetime | None = None  # wall-clock session end
    mode: Literal["simulate", "run"] = "simulate"
    auto_approved: bool = False  # True if simulate mode auto-approved all drafts


class LLMCall(BaseModel):
    """A single LLM API call record."""

    call_id: str
    timestamp: datetime
    node_name: str  # graph node that made the call; used for per-node cost attribution
    model: str  # e.g. "gpt-4o", "claude-3-opus" — drives cost calculation
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float = 0.0  # computed from model_pricing lookup, not API billing
    latency_ms: int
    prompt_hash: str = ""  # SHA-256 of the rendered prompt for dedup/debugging
    response_truncated: bool = False  # True if response hit max_tokens limit
    error: str | None = None  # populated when the API call failed


class IncidentResult(BaseModel):
    """The output of an incident session.

    Fields mirror the final state of IncidentState after the graph completes,
    but exclude intermediate/fragile fields (like current_update_draft).
    This is the serialisation boundary: what gets written to disk.
    """

    thread_id: str
    timeline: list[TimelineEvent] = Field(default_factory=list)
    stakeholder_updates: list[StakeholderUpdate] = Field(default_factory=list)
    remediation_suggestions: list[RemediationSuggestion] = Field(default_factory=list)
    deploy_correlations: list[DeployCorrelation] = Field(default_factory=list)
    postmortem: Postmortem | None = None
    cost_report: CostReport | None = None
    session_dir: str = ""

    def to_markdown(self) -> dict[str, str]:
        """Convert all output to markdown files (filename -> content).

        Produces 10 files per SPEC §13.5:
        incident-summary.md, timeline.md, stakeholder-updates.md, comms-blocks.md,
        remediation.md, postmortem.md, cost-report.md, llm-calls.jsonl,
        session.json, meta.json
        """
        return {
            "incident-summary.md": f"# Incident Summary: {self.thread_id}\n",
            "timeline.md": "\n".join(
                f"- [{e.timestamp}] [{e.source}] {e.content}" for e in self.timeline
            ),
            "stakeholder-updates.md": "\n\n".join(
                f"## Update {u.update_number}\n{u.impact}"
                for u in self.stakeholder_updates
            ),
            # comms-blocks.md: copy-paste-ready Slack/email blocks per update.
            # Uses Markdown formatting suitable for both Slack mrkdwn and
            # rendered HTML; the --- separator makes multi-update output
            # scannable.
            "comms-blocks.md": (
                "# Communication Blocks\n"
                + "\n\n---\n\n".join(
                    f"## Update {u.update_number}\n"
                    f"**Impact:** {u.impact}\n"
                    f"**Action:** {u.action}\n"
                    f"**Next Update:** {u.next_update_time}"
                    for u in self.stakeholder_updates
                )
                if self.stakeholder_updates
                else "# Communication Blocks\nNo updates drafted."
            ),
            "remediation.md": "\n\n".join(
                f"## {r.action}\n{r.citation}"
                for r in self.remediation_suggestions
            ),
            "postmortem.md": (
                f"# Postmortem: {self.postmortem.incident_id}\n"
                if self.postmortem
                else "# Postmortem: (none)\n"
            ),
            "cost-report.md": (
                f"# Cost Report: ${self.cost_report.total_estimated_cost_usd:.4f}\n"
                if self.cost_report
                else "# Cost Report: (none)\n"
            ),
        }

    # NOTE: spec §13.5 requires 10 output files; to_json() and to_markdown()
    # produce only 7 here. The remaining 3 (llm-calls.jsonl, session.json,
    # meta.json) are written separately by the SessionManager.

    def to_json(self) -> dict[str, Any]:
        """Export as JSON for programmatic consumption."""
        return self.model_dump()  # full Pydantic serialization including nested models
