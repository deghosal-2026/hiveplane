"""Stakeholder communication nodes — draft updates, produce comms, approve.

Nodes
-----
- ``draft_update_node`` — LLM generates a consequence-first stakeholder update.
- ``produce_output_node`` — assembles pasteable comms blocks from the draft.
- ``interrupt_for_approval`` — human-in-the-loop gate for reviewing drafts.
"""

from __future__ import annotations

import logging
from datetime import datetime

from incident_commander.models.state import IncidentState, StakeholderUpdate
from incident_commander.nodes.timeline import get_timeline_summary

from ._llm import get_llm_router

logger = logging.getLogger(__name__)


def _build_prompt(state: IncidentState) -> str:
    """Build the LLM prompt for drafting a stakeholder update."""
    alert_summary = state.alert.summary if state.alert else "Unknown alert"
    service = state.service or "unknown"
    severity = state.severity

    # Include deploy correlation context if available
    deploy_info = ""
    if state.deploy_correlations:
        lines = [
            f"  - PR #{c.pr_number}: {c.pr_title} ({c.correlation_strength})"
            for c in state.deploy_correlations
        ]
        deploy_info = "\nDeploy correlations:\n" + "\n".join(lines)

    # Include top 3 retrieved runbooks for context; limited to keep prompt
    # within context window while still grounding the LLM in documented steps.
    runbook_info = ""
    if state.retrieved_runbooks:
        top = state.retrieved_runbooks[:3]
        lines = [
            f"  - {r.get('title', 'Unknown')} (score: {r.get('relevance_score', 0)})"
            for r in top
        ]
        runbook_info = "\nRelevant runbooks:\n" + "\n".join(lines)

    timeline = get_timeline_summary(state)

    return f"""You are an incident commander assistant.
Draft a stakeholder update in consequence-first format.

INCIDENT: {alert_summary}
SERVICE: {service}
SEVERITY: {severity}

TIMELINE:
{timeline}
{deploy_info}
{runbook_info}

Format your response as:
IMPACT: <what's broken, who's affected>
ROOT_CAUSE: <current best guess>
ACTION: <what we're doing about it>
CONFIDENCE: <0.0-1.0>
"""


def _parse_response(
    response: str,
    update_number: int,
    next_update: datetime | None,
) -> StakeholderUpdate:
    """Parse the LLM response into a StakeholderUpdate object."""
    impact = ""
    root_cause = ""
    action = ""
    confidence = 0.5

    for line in response.splitlines():
        upper = line.upper().strip()
        if upper.startswith("IMPACT:"):
            impact = line.split(":", 1)[1].strip()
        elif upper.startswith("ROOT_CAUSE:"):
            root_cause = line.split(":", 1)[1].strip()
        elif upper.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
        elif upper.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
            except ValueError:
                confidence = 0.5

    # Fallback: if LLM returned unstructured text (ignored expected section
    # headers), use the entire response as the impact field so the caller
    # still gets some content instead of an empty draft.
    if not impact and not root_cause and not action:
        impact = response[:200] if response else "No update available."

    return StakeholderUpdate(
        update_number=update_number,
        impact=impact or "Impact assessment in progress.",
        root_cause_hypothesis=root_cause or "Root cause under investigation.",
        action=action or "Investigating.",
        next_update_time=next_update or datetime.now(),
        confidence=confidence,
        approved=False,
        timestamp=datetime.now(),
    )


def draft_update_node(state: IncidentState) -> IncidentState:
    """Generate a consequence-first stakeholder update via LLM.

    The draft is stored in ``state.current_update_draft`` and not yet
    marked as approved.  In simulate mode the caller may auto-approve.
    """
    router = get_llm_router()
    prompt = _build_prompt(state)

    try:
        response, _info = router.generate(prompt, task="comms")
    except Exception:
        logger.warning("LLM call failed in draft_update_node", exc_info=True)
        response = ""

    update_number = len(state.stakeholder_updates) + 1
    draft = _parse_response(response, update_number, state.next_update_time)
    state.current_update_draft = draft
    state.update_approved = False
    return state


def produce_output_node(state: IncidentState) -> IncidentState:
    """Finalise the approved draft into the stakeholder_updates list.

    This node runs after ``interrupt_for_approval`` approves the draft.
    The approved draft is appended to ``state.stakeholder_updates`` and
    ``state.last_update_time`` is updated.

    In simulate mode, the incident is marked as resolved after the first
    update so the graph proceeds to remediation and postmortem.  In run
    mode, the commander sets ``state.resolved = True`` manually via a
    UI interrupt when the incident is declared over.
    """
    if state.current_update_draft is None:
        # No draft to finalise — shouldn't happen in normal flow
        return state

    draft = state.current_update_draft
    draft.approved = True
    state.stakeholder_updates.append(draft)
    state.last_update_time = draft.timestamp
    state.current_update_draft = None
    state.update_approved = False

    # In simulate mode, mark incident resolved after first update so the
    # graph exits the update loop and proceeds to remediation + postmortem.
    # In run mode, the commander manually marks resolved via UI interrupt.
    if state.mode == "simulate":
        state.resolved = True

    return state


def interrupt_for_approval(state: IncidentState) -> IncidentState:
    """Human-in-the-loop gate for stakeholder update approval.

    SAFETY GUARDRAIL: In simulate mode, auto-approve all drafts.  In run
    mode, the LangGraph interrupt mechanism pauses here so a human can
    review the draft before it is sent to stakeholders.  The caller
    resumes with ``update_approved`` set by the human reviewer.
    """
    if state.mode == "simulate":
        # Auto-approve in simulation mode
        state.update_approved = True
    # In run mode, the LangGraph interrupt mechanism pauses here;
    # the caller resumes with update_approved set by the human
    return state
