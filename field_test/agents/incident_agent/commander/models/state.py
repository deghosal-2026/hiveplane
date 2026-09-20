"""LangGraph state schema and related models for incident-commander.

All Pydantic models that define the incident-commander data layer live here.
This module is the single import source for state, alert, timeline, input
channel types, cost, postmortem, and stakeholder models.

The input-channel types (ChatMessage, LogEntry, GitHubPR) are defined here
rather than in ``input.py`` to avoid a circular import: ``input.py`` needs
``Alert`` and ``TimelineEvent`` from this module, while ``IncidentState``
needs the three input types.  By co-locating them we break the cycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Trigger ──────────────────────────────────────────────────────────────

class Alert(BaseModel):
    """Parsed alert from JSON input — the trigger for an incident session."""

    severity: Literal["SEV1", "SEV2", "SEV3"]
    service: str
    summary: str
    source: str = "manual"  # default "manual" for test/dev; real alerts name the system
    timestamp: datetime
    incident_id: str = ""  # populated by upstream meta or auto-generated if empty
    metadata: dict[str, Any] = Field(default_factory=dict)  # preserves original payload


# ── Input channel types ──────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A chat message from Slack, Teams, or other chat export."""

    timestamp: datetime
    author: str
    text: str
    channel: str = ""  # empty means "no channel context" — caller may set later
    thread_ts: str | None = None  # Slack thread timestamp for reply grouping


class LogEntry(BaseModel):
    """A single parsed log entry from application or infrastructure logs."""

    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL", "TRACE"]  # TRACE: verbose debugging
    message: str
    source: str = ""  # empty source means the entry origin was not captured
    metadata: dict[str, Any] = Field(default_factory=dict)  # structured fields (e.g. trace_id)


class GitHubPR(BaseModel):
    """A GitHub pull request used for deploy correlation analysis."""

    number: int  # PR number on GitHub
    title: str
    author: str
    merge_time: datetime
    files_changed: list[str] = Field(default_factory=list)  # file paths modified by the PR
    labels: list[str] = Field(default_factory=list)  # GitHub labels (e.g. "hotfix", "deploy")
    base_branch: str = "main"  # target branch the PR was merged into


# ── Timeline ─────────────────────────────────────────────────────────────

class TimelineEvent(BaseModel):
    """A single event in the incident timeline — from any source."""

    timestamp: datetime
    source: Literal["alert", "chat", "log", "github", "manual"]  # originating channel
    event_type: str  # free-form label (e.g. "deploy", "error-spike", "mention")
    content: str
    trust_level: Literal["high", "medium", "low"]  # high=verifiable, low=hearsay/inferred
    deploy_correlation: bool = False  # True if event is a suspected deploy trigger


# ── Deploy correlation ───────────────────────────────────────────────────

class DeployCorrelation(BaseModel):
    """A GitHub PR/commit correlated with the incident via time proximity."""

    pr_number: int
    pr_title: str
    author: str
    merge_time: datetime
    files_changed: list[str] = Field(default_factory=list)
    minutes_before_alert: int  # positive; zero/negative filtered in deploy_correlation.py
    correlation_strength: Literal["strong", "weak"] = "strong"


# ── Stakeholder communication ────────────────────────────────────────────

class StakeholderUpdate(BaseModel):
    """A drafted stakeholder update in consequence-first format."""

    update_number: int  # sequential, starting at 1; used for dedup and ordering
    impact: str  # consequence-first: what's broken and who is affected
    root_cause_hypothesis: str  # best-guess root cause — may change between updates
    action: str  # what the incident commander is doing about it
    next_update_time: datetime  # when the next update is due (set by cadence node)
    confidence: float = 1.0  # 0-1 confidence in the hypothesis and impact assessment
    approved: bool = False  # gated by interrupt_for_approval
    timestamp: datetime


# ── Remediation ──────────────────────────────────────────────────────────

class RemediationSuggestion(BaseModel):
    """A remediation suggestion with citation and dry-run outcome."""

    action: str
    citation: str  # runbook or incident reference backing the suggestion
    confidence: float
    dry_run_outcome: str = ""  # result of simulating the fix (if applicable)
    similar_incidents: list[str] = Field(default_factory=list)  # historical incident IDs
    approved: bool = False


# ── Postmortem ───────────────────────────────────────────────────────────

class PostmortemSection(BaseModel):
    """A single section of the COE-format postmortem."""

    title: str
    content: str
    ai_generated: bool = True  # False when a human authored or edited the section


class ActionItem(BaseModel):
    """A corrective action from the postmortem with suggested owner."""

    description: str
    suggested_owner: str  # team or person the AI recommends assigning this item to
    priority: Literal["P0", "P1", "P2"] = "P1"  # P0 highest, P2 lowest urgency
    ai_generated: bool = True


class Postmortem(BaseModel):
    """Amazon COE (Correction of Errors) format postmortem.

    Severity-conditional sections:
    - SEV1: all 8 sections present
    - SEV2: omits regulatory + stakeholder_communication_log
    - SEV3: omits customer_impact + regulatory + stakeholder_communication_log

    The severity field is a plain ``str`` rather than a Literal, because
    the state may carry an unvalidated severity from user input. Validation
    is the caller's responsibility.
    """

    incident_id: str
    incident_date: datetime
    severity: str
    service: str

    # Core sections — always present regardless of severity
    summary: PostmortemSection
    timeline: PostmortemSection
    root_cause_analysis: PostmortemSection
    systemic_contributing_factors: PostmortemSection
    action_items: list[ActionItem]

    # Severity-conditional optional sections (None when omitted for lower SEVs)
    customer_impact: PostmortemSection | None = None
    stakeholder_communication_log: PostmortemSection | None = None
    regulatory_compliance_impact: PostmortemSection | None = None

    resolved_at: datetime | None = None  # proxy: last timeline event timestamp
    mttr_minutes: int | None = None  # computed as (resolved_at - alert.timestamp) in minutes
    approved: bool = False


# ── Cost tracking ────────────────────────────────────────────────────────

class NodeCost(BaseModel):
    """Cost breakdown for a single agent node (one LLM call)."""

    node_name: str  # graph node that made the call (e.g. "analyze_root_cause")
    llm_model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float  # derived from model_pricing lookup
    latency_ms: int  # wall-clock round-trip time for the API call


class CostReport(BaseModel):
    """Aggregate cost report for an entire incident session."""

    session_id: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    per_node: list[NodeCost]  # breakdown by individual LLM calls
    models_used: list[str]  # distinct model IDs invoked during the session


# ── Graph state ──────────────────────────────────────────────────────────

class IncidentState(BaseModel):
    """Full state schema for the incident commander LangGraph.

    This is the single state object threaded through every graph node.
    All input data, intermediate results, and final outputs live here.

    Design invariant: every field has a sensible default so that
    IncidentState() is valid at any point in the graph lifecycle.
    Nodes are responsible for populating only the fields they own.
    """

    # Alert that triggered this incident session
    alert: Alert | None = None
    severity: Literal["SEV1", "SEV2", "SEV3"] = "SEV3"  # mirrored from alert for routing
    service: str = ""  # mirrored from alert for quick access by nodes
    incident_id: str = ""  # mirrored from alert/meta for log correlation

    # Immutable input data — set once at session start
    input_logs: list[LogEntry] = Field(default_factory=list)
    input_messages: list[ChatMessage] = Field(default_factory=list)  # chat history
    input_prs: list[GitHubPR] = Field(default_factory=list)  # recently merged PRs
    input_manual_events: list[TimelineEvent] = Field(default_factory=list)  # human-added

    # Derived data — built up as the graph executes
    timeline: list[TimelineEvent] = Field(default_factory=list)  # merged chronological events
    deploy_correlations: list[DeployCorrelation] = Field(default_factory=list)

    # RAG retrieval results
    retrieved_runbooks: list[dict[str, Any]] = Field(default_factory=list)  # matched runbooks
    retrieved_incidents: list[dict[str, Any]] = Field(default_factory=list)  # past incidents
    reranked_evidence: list[dict[str, Any]] = Field(default_factory=list)  # post-rerank top-k

    # Stakeholder communication workflow
    stakeholder_updates: list[StakeholderUpdate] = Field(default_factory=list)  # sent/approved
    current_update_draft: StakeholderUpdate | None = None  # pending draft awaiting approval
    update_approved: bool = False  # gate flag for the comms node

    # Remediation workflow
    remediation_suggestions: list[RemediationSuggestion] = Field(default_factory=list)
    current_remediation: RemediationSuggestion | None = None  # pending suggestion
    remediation_approved: bool = False  # gate flag for the remediation node

    # Postmortem workflow
    postmortem: Postmortem | None = None  # generated after resolution
    postmortem_approved: bool = False  # gate flag for the postmortem node

    # Cost tracking
    cost_report: CostReport | None = None  # populated by cost_report_node after graph exit

    # Session metadata and routing
    thread_id: str = ""  # LangGraph thread checkpoint key; must be unique per session
    mode: Literal["simulate", "run"] = "simulate"  # defaults to simulate for safety
    resolved: bool = False  # set True when incident is declared over

    # Stakeholder update scheduling
    last_update_time: datetime | None = None  # timestamp of most recent sent update
    next_update_time: datetime | None = None  # set by cadence_timer_node; drives update loop
