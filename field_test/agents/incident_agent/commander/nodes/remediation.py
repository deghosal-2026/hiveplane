"""Remediation nodes — suggest actions, simulate outcomes, approve.

Nodes
-----
- ``suggest_remediation_node`` — pattern-matches past incidents and runbooks
  to propose a remediation action with citation and confidence.
- ``dry_run_simulate_node`` — LLM simulates the expected outcome of the
  suggested action (text prediction, NOT code execution).
- ``interrupt_for_remediation_review`` — human-in-the-loop gate.
"""

from __future__ import annotations

import logging

from incident_commander.config import Config
from incident_commander.models.state import (
    IncidentState,
    RemediationSuggestion,
)

from ._llm import get_llm_router

logger = logging.getLogger(__name__)

# Module-level config for confidence threshold
_config: Config | None = None


def init_config(config: Config) -> None:
    """Set the module-level config used by remediation nodes."""
    global _config  # noqa: PLW0603
    _config = config


def _get_threshold() -> float:
    """Return the confidence threshold from config (default 0.7)."""
    if _config is not None:
        return _config.confidence_threshold
    return 0.7  # Conservative default: require 70% confidence before surfacing


def _build_remediation_prompt(state: IncidentState) -> str:
    """Build the LLM prompt for suggesting a remediation action."""
    alert_summary = state.alert.summary if state.alert else "Unknown"
    service = state.service or "unknown"

    # Include reranked evidence for context
    evidence_lines = []
    for item in state.reranked_evidence[:5]:
        title = item.get("title", "Unknown")
        citation = item.get("citation", "")
        evidence_lines.append(f"  - {title} (source: {citation})")

    evidence = "\n".join(evidence_lines) if evidence_lines else "  No relevant evidence found."

    # Include deploy correlations
    deploy_info = ""
    if state.deploy_correlations:
        c = state.deploy_correlations[0]
        deploy_info = (
            f"\nDeploy correlation: PR #{c.pr_number} ({c.pr_title})"
            f" — {c.correlation_strength}"
        )

    return f"""You are an incident remediation advisor. Suggest ONE remediation action.

INCIDENT: {alert_summary}
SERVICE: {service}
{deploy_info}

EVIDENCE:
{evidence}

Format your response as:
ACTION: <recommended action>
CITATION: <source: runbook name or past incident ID>
CONFIDENCE: <0.0-1.0>
SIMILAR_INCIDENTS: <comma-separated incident IDs, or "none">
"""


def _parse_remediation_response(response: str) -> RemediationSuggestion:
    """Parse the LLM response into a RemediationSuggestion."""
    action = ""
    citation = ""
    confidence = 0.5  # Neutral midpoint if parsing fails
    similar: list[str] = []

    for line in response.splitlines():
        upper = line.upper().strip()
        if upper.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
        elif upper.startswith("CITATION:"):
            citation = line.split(":", 1)[1].strip()
        elif upper.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
            except ValueError:
                confidence = 0.5  # Parse failure → neutral midpoint
        elif upper.startswith("SIMILAR_INCIDENTS:"):
            raw = line.split(":", 1)[1].strip()
            if raw.lower() != "none":
                similar = [s.strip() for s in raw.split(",") if s.strip()]

    # ── SAFETY GUARDRAIL: Citation enforcement ─────────────────────────
    # Every remediation suggestion MUST include a source citation (runbook
    # or past incident ID).  Suggestions without a citation are rejected
    # to prevent ungrounded LLM hallucinations from reaching production.
    if not citation:
        logger.warning("Remediation suggestion missing citation — rejecting")
        return RemediationSuggestion(
            action="No suggestion — missing citation",
            citation="",
            confidence=0.0,
        )

    # Normalize citation to start with "Source:" for consistent formatting
    if citation and not citation.startswith("Source:"):
        citation = f"Source: {citation}"

    return RemediationSuggestion(
        # Fallback to generic text if LLM returned structured format but
        # empty action — shouldn't happen with a well-formed response.
        action=action or "No action suggested.",
        citation=citation,
        confidence=confidence,
        similar_incidents=similar,
    )


def suggest_remediation_node(state: IncidentState) -> IncidentState:
    """Generate a remediation suggestion via LLM with citation enforcement.

    Every suggestion must include a source citation.  Suggestions with
    confidence below the configured threshold are suppressed.
    """
    router = get_llm_router()
    prompt = _build_remediation_prompt(state)

    try:
        response, _info = router.generate(prompt, task="analysis")
    except Exception:
        logger.warning("LLM call failed in suggest_remediation_node", exc_info=True)
        response = ""

    suggestion = _parse_remediation_response(response)

    # ── SAFETY GUARDRAIL: Confidence threshold ────────────────────────
    # Suggestions below the configured confidence threshold (default 0.7)
    # are suppressed to avoid surfacing low-probability remediation actions
    # that could waste the incident commander's time or cause harm.
    # Skip this check if the suggestion was already rejected (missing citation).
    threshold = _get_threshold()
    if suggestion.citation and suggestion.confidence < threshold:
        logger.info(
            "Remediation suppressed — confidence %.2f < threshold %.2f",
            suggestion.confidence,
            threshold,
        )
        suggestion = RemediationSuggestion(
            action="No suggestion — confidence below threshold",
            citation="",
            confidence=suggestion.confidence,
        )

    # Ensure similar_incidents is an empty list (not None) so downstream
    # code can iterate without null checks.
    if not suggestion.similar_incidents and suggestion.citation:
        suggestion.similar_incidents = []

    state.current_remediation = suggestion
    state.remediation_approved = False
    return state


def _build_dry_run_prompt(state: IncidentState) -> str:
    """Build the LLM prompt for simulating the expected outcome."""
    suggestion = state.current_remediation
    if suggestion is None:
        return ""

    alert_summary = state.alert.summary if state.alert else "Unknown"
    service = state.service or "unknown"

    return f"""You are an incident simulator. Predict the expected outcome of a remediation action.

INCIDENT: {alert_summary}
SERVICE: {service}
PROPOSED ACTION: {suggestion.action}

Format your response as a brief prediction of what would happen if this action is taken.
Example: "payment success rate should return to >99% within 2 minutes of rollback"
"""


def dry_run_simulate_node(state: IncidentState) -> IncidentState:
    """LLM simulates the expected outcome of the proposed remediation.

    SAFETY GUARDRAIL: This is a TEXT PREDICTION only — the LLM describes
    what it expects would happen.  It NEVER executes code, runs shell
    commands, makes API calls, or touches production systems.
    """
    if state.current_remediation is None:
        return state

    router = get_llm_router()
    prompt = _build_dry_run_prompt(state)

    try:
        response, _info = router.generate(prompt, task="analysis")
    except Exception:
        logger.warning("LLM call failed in dry_run_simulate_node", exc_info=True)
        response = ""

    state.current_remediation.dry_run_outcome = response or "Outcome prediction unavailable."
    return state


def interrupt_for_remediation_review(state: IncidentState) -> IncidentState:
    """Human-in-the-loop gate for remediation review.

    SAFETY GUARDRAIL: In simulate mode, auto-approve.  In run mode, the
    LangGraph interrupt mechanism pauses for human review so the commander
    can evaluate the suggestion and its dry-run outcome before actioning.
    """
    if state.mode == "simulate":
        state.remediation_approved = True
        if state.current_remediation is not None:
            state.current_remediation.approved = True
            state.remediation_suggestions.append(state.current_remediation)
    return state
