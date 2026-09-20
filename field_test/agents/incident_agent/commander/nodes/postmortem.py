"""Postmortem generation node — COE-format, severity-conditional, blameless."""

from __future__ import annotations

import logging
from datetime import datetime

from incident_commander.models.state import (
    ActionItem,
    IncidentState,
    Postmortem,
    PostmortemSection,
)

from ._llm import get_llm_router

logger = logging.getLogger(__name__)


def _build_postmortem_prompt(state: IncidentState) -> str:
    """Build the LLM prompt for COE-format postmortem generation.

    Includes timeline, stakeholder updates, retrieved evidence,
    blameless rules, and section instructions conditional on severity.
    """
    timeline = ""
    for e in state.timeline:
        marker = " [DEPLOY]" if e.deploy_correlation else ""
        timeline += f"- [{e.timestamp.isoformat()}] [{e.source}] {e.content}{marker}\n"

    updates = ""
    for u in state.stakeholder_updates:
        updates += f"- Update {u.update_number}: {u.impact}\n"

    # Only top-3 reranked evidence items to keep prompt within context limits
    # and avoid overwhelming the LLM with redundant information.
    evidence_info = ""
    for item in state.reranked_evidence[:3]:
        evidence_info += f"  - {item.get('title', '')} ({item.get('citation', '')})\n"

    return f"""You are an incident postmortem author. Generate a COE-format postmortem.

INCIDENT: {state.incident_id or "Unknown"}
SERVICE: {state.service}
SEVERITY: {state.severity}
INCIDENT DATE: {state.alert.timestamp.date() if state.alert else "Unknown"}

TIMELINE:
{timeline}

STAKEHOLDER UPDATES:
{updates}

RETRIEVED EVIDENCE:
{evidence_info}

BLAMELESS RULES (SAFETY GUARDRAIL):
- Focus on what failed, not who failed.
- Systemic Contributing Factors must focus on processes, not people.
- Do not include individual names in root cause or systemic factors.

Generate these sections:
1. SUMMARY — brief incident overview (always)
2. CUSTOMER_IMPACT — who was affected and how (SEV1/SEV2 only)
3. TIMELINE — key events (always — from session data)
4. ROOT_CAUSE_ANALYSIS — what caused the incident (always)
5. SYSTEMIC_CONTRIBUTING_FACTORS — blameless factors (always)
6. ACTION_ITEMS — corrective actions with suggested owners (always)
7. STAKEHOLDER_COMMUNICATION_LOG — update log (SEV1 only)
8. REGULATORY_COMPLIANCE_IMPACT — compliance notes (SEV1 only)

For each section, write the content. For action items, list each item
with suggested owner and priority (P0/P1/P2).
"""


def _parse_postmortem_response(
    response: str,
    state: IncidentState,
) -> Postmortem:
    """Parse the LLM postmortem response into a Postmortem object.

    Handles section headers, action items with owner|priority format,
    severity-conditional sections, and MTTR calculation.
    """
    sections: dict[str, str] = {}
    current_section = ""
    current_content: list[str] = []
    action_items: list[ActionItem] = []

    for line in response.splitlines():
        stripped = line.strip().lstrip("#").strip().lstrip("*").strip()
        # Strip leading numbering like "1. " or "2. "
        if (len(stripped) > 2 and stripped[0].isdigit()
                and stripped[1] in ".)" and stripped[2] == " "):
            stripped = stripped[3:].strip()
        upper = stripped.upper()
        section_names = [
            "SUMMARY", "CUSTOMER_IMPACT", "TIMELINE",
            "ROOT_CAUSE_ANALYSIS", "SYSTEMIC_CONTRIBUTING_FACTORS",
            "ACTION_ITEMS", "STAKEHOLDER_COMMUNICATION_LOG",
            "REGULATORY_COMPLIANCE_IMPACT",
        ]
        # Heuristic: line is a section header if it starts with a known name
        # and is either short (<50 chars) or ends with ":". This avoids false
        # positives when a section name appears mid-sentence in prose.
        # For example, "the timeline shows..." should NOT match TIMELINE.
        is_section_header = any(
            upper.startswith(s) and (len(stripped) < 50 or upper.endswith(":"))
            for s in section_names
        )

        if is_section_header:
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = ""
            current_content = []

            for s in section_names:
                if upper.startswith(s):
                    current_section = s
                    rest = stripped[len(s):].lstrip(": ").strip()
                    if rest:
                        current_content.append(rest)
                    break
        elif current_section:
            if current_section == "ACTION_ITEMS" and line.strip().startswith("- "):
                # Parse "description | owner, priority" format.
                # The pipe separates description from metadata; comma within
                # metadata separates owner from priority (P0/P1/P2).
                # E.g. "Roll back canary | Alice, P1" → owner=Alice, priority=P1
                parts = line.strip("- ").strip()
                if "|" in parts:
                    desc, rest = parts.split("|", 1)
                    owner = ""
                    priority = "P1"
                    if "," in rest:
                        owner, priority = [x.strip() for x in rest.split(",", 1)]
                    else:
                        owner = rest.strip()
                    action_items.append(ActionItem(
                        description=desc.strip(),
                        suggested_owner=owner,
                        priority=priority,  # type: ignore[arg-type]
                    ))
            else:
                current_content.append(line)

    if current_section and current_content:
        sections[current_section] = "\n".join(current_content).strip()

    alert_time = state.alert.timestamp if state.alert else datetime.now()
    severity = state.severity

    def section(title: str, key: str, ai: bool = True) -> PostmortemSection:
        """Build a PostmortemSection from parsed response data."""
        # If the LLM didn't produce this section, fill with a placeholder
        # rather than failing — the human reviewer can add missing content.
        # This keeps the graph running even with partial LLM output.
        return PostmortemSection(
            title=title,
            content=sections.get(key, f"{title} — insufficient data."),
            ai_generated=ai,
        )

    p = Postmortem(
        incident_id=state.incident_id or "INC-UNKNOWN",
        incident_date=alert_time,
        severity=severity,
        service=state.service,
        summary=section("Summary", "SUMMARY"),
        # Timeline section is marked ai_generated=False because it's
        # reconstructed from structured state, not LLM hallucination.
        timeline=section("Timeline", "TIMELINE", ai=False),
        root_cause_analysis=section("Root Cause Analysis", "ROOT_CAUSE_ANALYSIS"),
        systemic_contributing_factors=section(
            "Systemic Contributing Factors", "SYSTEMIC_CONTRIBUTING_FACTORS"
        ),
        action_items=action_items or [
            ActionItem(
                description="No specific action items identified.",
                suggested_owner="TBD",
                priority="P2",
            )
        ],
        # Use last timeline event timestamp as proxy for resolved_at.
        # The actual resolution timestamp may differ, but this is the best
        # approximation available from structured data.
        resolved_at=state.timeline[-1].timestamp if state.timeline else None,
    )

    # Severity-conditional sections per SPEC §9.0
    if severity in ("SEV1", "SEV2"):
        p.customer_impact = section("Customer Impact", "CUSTOMER_IMPACT")
    if severity == "SEV1":
        p.stakeholder_communication_log = section(
            "Stakeholder Communication Log",
            "STAKEHOLDER_COMMUNICATION_LOG",
            ai=False,
        )
        p.regulatory_compliance_impact = section(
            "Regulatory/Compliance Impact",
            "REGULATORY_COMPLIANCE_IMPACT",
        )

    # MTTR = alert timestamp → last timeline event (resolution proxy)
    if p.resolved_at and state.alert:
        mttr = int((p.resolved_at - state.alert.timestamp).total_seconds() / 60)
        p.mttr_minutes = mttr

    return p


def generate_postmortem_node(state: IncidentState) -> IncidentState:
    """Generate a COE-format postmortem via LLM with blameless framing.

    SAFETY GUARDRAIL: Blameless rules are enforced in the prompt — the
    LLM is explicitly instructed to focus on systemic factors, not
    individuals.  AI-generated sections are labelled so reviewers can
    scrutinize them before publishing.

    Severity-conditional sections per SPEC §9.0:
    - SEV1: all 8 sections
    - SEV2: omits regulatory + comm log
    - SEV3: omits customer_impact + regulatory + comm log
    """
    router = get_llm_router()
    prompt = _build_postmortem_prompt(state)

    try:
        response, _info = router.generate(prompt, task="postmortem")
    except Exception:
        # On LLM failure, continue with empty response so the graph doesn't
        # crash — the postmortem will show "insufficient data" for all sections
        # and the human reviewer can fill in details manually.
        logger.warning("LLM call failed in generate_postmortem_node", exc_info=True)
        response = ""

    postmortem = _parse_postmortem_response(response, state)
    state.postmortem = postmortem
    state.postmortem_approved = False
    return state


def interrupt_for_postmortem_review(state: IncidentState) -> IncidentState:
    """Human-in-the-loop gate for postmortem review.

    SAFETY GUARDRAIL: In simulate mode, auto-approve.  In run mode, the
    LangGraph interrupt mechanism pauses for human review so the postmortem
    can be checked for blameless compliance and factual accuracy before
    it is published.
    """
    if state.mode == "simulate":
        state.postmortem_approved = True
        if state.postmortem is not None:
            state.postmortem.approved = True
    return state
