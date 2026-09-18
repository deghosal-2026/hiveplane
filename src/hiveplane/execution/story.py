"""The run execution story: a single ordered view of what a run did (M20, #51).

The story is assembled from the control plane's own records — admission, state
transitions, policy decisions, tool calls, model calls, sandbox events,
deliveries, and approvals — so an operator can read a run end to end. When the
run started under an active trace, ``trace_id`` links to the full OTel trace.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import AdmissionResult, DeliveryRecord

_EVENT_KIND: dict[EventType, str] = {
    EventType.STATE_CHANGE: "state",
    EventType.POLICY_DECISION: "policy_decision",
    EventType.TOOL_CALL: "tool_call",
    EventType.OPERATOR_ACTION: "operator_action",
    EventType.SANDBOX: "sandbox",
}


class StoryEntry(BaseModel):
    """One attributed moment in a run's execution story."""

    model_config = ConfigDict(extra="forbid")

    timestamp: AwareDatetime
    kind: str
    summary: str
    detail: dict[str, JsonValue] = Field(default_factory=dict)


class RunStory(BaseModel):
    """A run's full execution story, with correlation and trace linkage."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workload: str
    team: str | None
    state: RunState
    context: AdmissionContext | None
    model_identity: str | None
    cost_usd: float
    sandbox: bool
    sandbox_id: str | None
    certification_status: str
    attestation_id: str | None
    trace_id: str | None
    entries: list[StoryEntry]


def build_run_story(
    *,
    run: Run,
    workload: AgentWorkload,
    admission: AdmissionResult | None = None,
    events: Sequence[RunEvent] = (),
    usage: Sequence[UsageReport] = (),
    deliveries: Sequence[DeliveryRecord] = (),
    approvals: Sequence[ApprovalRecord] = (),
) -> RunStory:
    """Assemble a run's execution story from its persisted records."""
    entries: list[StoryEntry] = []
    if admission is not None:
        entries.append(_admission_entry(admission, run.created_at))
    for event in events:
        entry = _event_entry(event)
        if entry is not None:
            entries.append(entry)
    entries.extend(_usage_entry(report) for report in usage)
    entries.extend(_delivery_entry(record) for record in deliveries)
    entries.extend(_approval_entry(record) for record in approvals)
    entries.sort(key=lambda entry: entry.timestamp)

    certification = workload.spec.certification
    return RunStory(
        run_id=run.id,
        workload=run.workload_id,
        team=workload.team,
        state=run.state,
        context=run.context,
        model_identity=run.model_identity,
        cost_usd=run.cost_usd,
        sandbox=run.sandbox,
        sandbox_id=run.sandbox_id,
        certification_status=workload.certification_status.value,
        attestation_id=certification.attestation_id if certification else None,
        trace_id=run.trace_id,
        entries=entries,
    )


def _admission_entry(admission: AdmissionResult, timestamp: datetime) -> StoryEntry:
    return StoryEntry(
        timestamp=timestamp,
        kind="admission",
        summary=admission.outcome.value,
        detail={
            "outcome": admission.outcome.value,
            "refused_reason": admission.refused_reason,
            "escalation_required": admission.escalation_required,
            "sandbox": admission.sandbox,
            "checks": [check.model_dump(mode="json") for check in admission.checks],
        },
    )


def _event_entry(event: RunEvent) -> StoryEntry | None:
    kind = _EVENT_KIND.get(event.type)
    if kind is None:
        return None
    if event.type is EventType.STATE_CHANGE:
        source = event.from_state.value if event.from_state else "?"
        target = event.to_state.value if event.to_state else "?"
        summary = f"{source} -> {target}"
    else:
        summary = event.detail or event.type.value
    return StoryEntry(
        timestamp=event.timestamp,
        kind=kind,
        summary=summary,
        detail={
            "actor": event.actor,
            "detail": event.detail,
            "from_state": event.from_state.value if event.from_state else None,
            "to_state": event.to_state.value if event.to_state else None,
        },
    )


def _usage_entry(report: UsageReport) -> StoryEntry:
    tokens = report.input_tokens + report.output_tokens
    return StoryEntry(
        timestamp=report.timestamp,
        kind="model_call",
        summary=f"{tokens} tokens, ${report.cost_usd:.4f}",
        detail={
            "model_identity": report.model_identity,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "tool_calls": report.tool_calls,
            "cost_usd": report.cost_usd,
        },
    )


def _delivery_entry(record: DeliveryRecord) -> StoryEntry:
    return StoryEntry(
        timestamp=record.timestamp,
        kind="delivery",
        summary=f"{record.destination_type.value} {record.status.value}",
        detail={
            "target": record.target,
            "status": record.status.value,
            "attempts": record.attempts,
            "error": record.error,
        },
    )


def _approval_entry(record: ApprovalRecord) -> StoryEntry:
    return StoryEntry(
        timestamp=record.requested_at,
        kind="approval",
        summary=f"{record.rule} {record.status.value}",
        detail={
            "approval_id": record.approval_id,
            "rule": record.rule,
            "reason": record.reason,
            "status": record.status.value,
            "decided_by": record.decided_by,
            "decision_reason": record.decision_reason,
        },
    )
