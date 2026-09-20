"""LangGraph state graph definition for ai-incident-commander.

Graph flow (15 nodes, 4 conditional edges):

    build_timeline → correlate_deploys → retrieve_runbooks → rerank_evidence
        → cadence_timer → draft_update → interrupt_for_approval
            │ approved → produce_output → _is_resolved?
            │   resolved → suggest_remediation → dry_run_simulate
            │       → interrupt_for_remediation_review
            │           │ approved → generate_postmortem
            │           │       → interrupt_for_postmortem_review
            │           │           │ approved → cost_report → END
            │           │           │ rejected → generate_postmortem (retry)
            │           │ rejected → suggest_remediation (retry)
            │   not resolved → cadence_timer (loop for next update)
            │ rejected → draft_update (retry)

Safety guardrails (enforced in nodes, not in graph wiring):
  - Remediation citations mandatory (reject if missing) — remediation.py §106
  - Confidence threshold suppresses low-confidence suggestions — remediation.py §140
  - Dry-run is text prediction only, NEVER code execution — remediation.py §183
  - Postmortem blameless rules enforced in prompt — postmortem.py §50
  - AI-generated sections labelled for reviewer scrutiny — formatters.py §127
  - Three human-in-the-loop interrupt gates (update, remediation, postmortem)
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from langgraph.graph import END, StateGraph

from incident_commander.config import Config
from incident_commander.ingest.input_dir import InputDirLoader
from incident_commander.llm_router import LLMRouter, MockLLM
from incident_commander.models.state import IncidentState
from incident_commander.nodes.cadence import cadence_timer_node
from incident_commander.nodes.cadence import init_config as init_cadence_config
from incident_commander.nodes.cost_report import cost_report_node
from incident_commander.nodes.deploy_correlation import correlate_deploys_node
from incident_commander.nodes.postmortem import (
    generate_postmortem_node,
    interrupt_for_postmortem_review,
)
from incident_commander.nodes.rag import (
    InMemoryRetriever,
    init_retriever,
    retrieve_runbooks_node,
)
from incident_commander.nodes.remediation import (
    dry_run_simulate_node,
    interrupt_for_remediation_review,
    suggest_remediation_node,
)
from incident_commander.nodes.remediation import (
    init_config as init_remediation_config,
)
from incident_commander.nodes.rerank import rerank_evidence_node
from incident_commander.nodes.stakeholder import (
    draft_update_node,
    interrupt_for_approval,
    produce_output_node,
)
from incident_commander.nodes.timeline import build_timeline_node

from .nodes._llm import init_llm_router  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# ── Conditional edge routers ──────────────────────────────────────────────


def _is_resolved(state: IncidentState) -> Literal["remediate", "continue"]:
    """Conditional edge: check if the incident is marked resolved.

    Returns ``"remediate"`` to proceed to remediation + postmortem, or
    ``"continue"`` to loop back for another stakeholder update cycle.
    In simulate mode, ``produce_output_node`` sets ``resolved = True``
    after the first update so the graph exits the loop automatically.
    """
    # resolved is toggled by produce_output_node — in simulate mode it's
    # always set to True after one pass; in run mode it stays False until
    # a human explicitly marks the incident as resolved.
    if state.resolved:
        return "remediate"
    return "continue"


def _approval_router(
    state: IncidentState,
) -> Literal["approved", "rejected"]:
    """Conditional edge: route based on stakeholder update approval status.

    SAFETY GUARDRAIL: In run mode, a human must approve the draft before
    it is sent to stakeholders.  Rejection loops back to redraft.
    """
    if state.update_approved:
        return "approved"
    return "rejected"


def _remediation_router(
    state: IncidentState,
) -> Literal["approved", "rejected"]:
    """Conditional edge: route based on remediation approval.

    SAFETY GUARDRAIL: In run mode, a human must review the remediation
    suggestion (and its dry-run outcome) before it is actioned.
    Rejection loops back for a new suggestion.
    """
    if state.remediation_approved:
        return "approved"
    return "rejected"


def _postmortem_router(
    state: IncidentState,
) -> Literal["approved", "rejected"]:
    """Conditional edge: route based on postmortem approval.

    SAFETY GUARDRAIL: In run mode, a human must review the postmortem
    before it is published.  Rejection loops back for regeneration.
    """
    if state.postmortem_approved:
        return "approved"
    return "rejected"


# ── Graph builder ─────────────────────────────────────────────────────────


def build_graph(
    config: Config | None = None,
    mock_llm: MockLLM | None = None,
    input_data: dict[str, object] | None = None,
) -> StateGraph:  # type: ignore[type-arg]
    """Build and compile the incident-commander LangGraph.

    Args:
        config: Application configuration.  Uses defaults when ``None``.
        mock_llm: Optional mock callable for testing without real LLM calls.
        input_data: Optional pre-parsed input data for the initial state.

    Returns:
        A compiled ``StateGraph`` ready for invocation.

    """
    cfg = config or Config()
    router = LLMRouter(config=cfg, mock_llm=mock_llm)

    # Initialize module-level dependencies that nodes rely on via getters.
    # This pattern avoids passing the router/config through every node signature,
    # keeping node functions as plain callables that satisfy LangGraph's node
    # interface without currying or closures.
    init_llm_router(router)
    init_cadence_config(cfg)
    init_remediation_config(cfg)

    # Initialize the RAG retriever with runbooks from input data (if any).
    # InMemoryRetriever is a simple list-based retriever; a Qdrant-backed
    # retriever would be swapped in here when qdrant_url is configured.
    runbooks = input_data.get("runbooks", []) if input_data else []
    init_retriever(retriever=InMemoryRetriever(), runbooks=cast("list[Any]", runbooks))

    # ── Build the state graph ──────────────────────────────────────────
    workflow = StateGraph(IncidentState)

    # ── Register all 14 nodes ──────────────────────────────────────────
    # Phase 1: Data fusion — merge multi-source inputs into a unified timeline
    workflow.add_node("build_timeline", build_timeline_node)
    workflow.add_node("correlate_deploys", correlate_deploys_node)

    # Phase 2: RAG retrieval — fetch and rerank relevant runbooks/evidence
    workflow.add_node("retrieve_runbooks", retrieve_runbooks_node)
    workflow.add_node("rerank_evidence", rerank_evidence_node)

    # Phase 3: Stakeholder communication — cadence-driven update cycle
    workflow.add_node("cadence_timer", cadence_timer_node)
    workflow.add_node("draft_update", draft_update_node)
    workflow.add_node("interrupt_for_approval", interrupt_for_approval)
    workflow.add_node("produce_output", produce_output_node)

    # Phase 4: Remediation — suggest action + simulate outcome
    workflow.add_node("suggest_remediation", suggest_remediation_node)
    workflow.add_node("dry_run_simulate", dry_run_simulate_node)
    workflow.add_node("interrupt_for_remediation_review", interrupt_for_remediation_review)

    # Phase 5: Postmortem — COE-format blameless report
    workflow.add_node("generate_postmortem", generate_postmortem_node)
    workflow.add_node("interrupt_for_postmortem_review", interrupt_for_postmortem_review)

    # Phase 6: Cost aggregation
    workflow.add_node("cost_report", cost_report_node)

    # ── Wire edges ─────────────────────────────────────────────────────

    # Entry point: start with timeline building
    workflow.set_entry_point("build_timeline")

    # Linear flow: timeline → deploy correlation → RAG retrieval → rerank
    workflow.add_edge("build_timeline", "correlate_deploys")
    workflow.add_edge("correlate_deploys", "retrieve_runbooks")
    workflow.add_edge("retrieve_runbooks", "rerank_evidence")

    # Rerank → cadence timer → draft update → human approval gate
    workflow.add_edge("rerank_evidence", "cadence_timer")
    workflow.add_edge("cadence_timer", "draft_update")
    workflow.add_edge("draft_update", "interrupt_for_approval")

    # ── Conditional edge 1: stakeholder update approval ────────────────
    # approved → produce output (finalize the update)
    # rejected → draft_update (redraft with same context)
    workflow.add_conditional_edges(
        "interrupt_for_approval",
        _approval_router,
        {
            "approved": "produce_output",
            "rejected": "draft_update",
        },
    )

    # ── Conditional edge 2: resolved check ─────────────────────────────
    # resolved → suggest_remediation (proceed to remediation phase)
    # not resolved → cadence_timer (loop back for next update cycle)
    workflow.add_conditional_edges(
        "produce_output",
        _is_resolved,
        {
            "remediate": "suggest_remediation",
            "continue": "cadence_timer",
        },
    )

    # Remediation flow: suggest → dry-run → human review
    workflow.add_edge("suggest_remediation", "dry_run_simulate")
    workflow.add_edge("dry_run_simulate", "interrupt_for_remediation_review")

    # ── Conditional edge 3: remediation approval ───────────────────────
    # approved → generate_postmortem (proceed to postmortem phase)
    # rejected → suggest_remediation (generate a new suggestion)
    workflow.add_conditional_edges(
        "interrupt_for_remediation_review",
        _remediation_router,
        {
            "approved": "generate_postmortem",
            "rejected": "suggest_remediation",
        },
    )

    # Postmortem flow: generate → human review
    workflow.add_edge("generate_postmortem", "interrupt_for_postmortem_review")

    # ── Conditional edge 4: postmortem approval ────────────────────────
    # approved → cost_report (proceed to final cost aggregation)
    # rejected → generate_postmortem (regenerate with adjustments)
    workflow.add_conditional_edges(
        "interrupt_for_postmortem_review",
        _postmortem_router,
        {
            "approved": "cost_report",
            "rejected": "generate_postmortem",
        },
    )

    # Terminal: cost report → END
    workflow.add_edge("cost_report", END)

    return workflow.compile()  # type: ignore[return-value]


# ── Convenience runner ────────────────────────────────────────────────────


def build_and_run(
    input_dir: str | None = None,
    config: Config | None = None,
    mock_llm: MockLLM | None = None,
    auto_approve: bool = False,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:  # noqa: ANN401
    """Build the graph, compile it, and run with input data.

    Args:
        input_dir: Path to a structured input directory.
        config: Application configuration.
        mock_llm: Optional mock callable for testing.
        auto_approve: Skip all human-in-the-loop gates (sets mode="simulate").
        input_data: Pre-parsed input data dict (skips loading from input_dir
            or simulator).  Used by ``run_incident()`` after normalizing.

    Returns:
        The final state dict after graph execution.

    """
    cfg = config or Config()
    if auto_approve:
        cfg.mode = "simulate"

    # Load input data: explicit > input_dir > default simulator
    # This three-tier fallback lets callers provide data at the granularity
    # they prefer — pre-parsed dict, directory of files, or auto-generated.
    if input_data is None:
        if input_dir:
            loader = InputDirLoader(input_dir)
            incident_input = loader.load()
            runbooks = incident_input.runbooks
            input_data = {
                "alert": incident_input.alert,
                "logs": incident_input.logs,
                "messages": incident_input.messages,
                "github": incident_input.github,
                "runbooks": runbooks,
                "manual_events": incident_input.manual_events,
            }
        else:
            # No input directory → use default simulator with SEV1 payment-service
            from incident_commander.simulation.simulator import IncidentSimulator
            sim = IncidentSimulator(seed=42)
            simulated = sim.simulate("payment-service", "SEV1")
            input_data = {
                "alert": simulated.alert,
                "logs": simulated.logs,
                "messages": simulated.messages,
                "github": simulated.github,
                "runbooks": simulated.runbooks,
                "manual_events": simulated.manual_events,
            }

    # Build the graph with the loaded input data
    graph = build_graph(config=cfg, mock_llm=mock_llm, input_data=input_data)

    # Construct the initial state from loaded input data.
    # incident_id and thread_id are auto-generated if the input alert lacks them,
    # ensuring every run produces a traceable, unique session.
    import uuid
    initial_state = IncidentState(
        alert=input_data["alert"],
        severity=input_data["alert"].severity,
        service=input_data["alert"].service,
        incident_id=input_data["alert"].incident_id or f"INC-{uuid.uuid4().hex[:8]}",
        thread_id=f"thread-{uuid.uuid4().hex[:12]}",
        input_logs=input_data["logs"],
        input_messages=input_data["messages"],
        input_prs=input_data["github"],
        input_manual_events=input_data["manual_events"],
        mode="simulate" if auto_approve else cfg.mode,
    )

    # Invoke the graph and return the final state
    result = graph.invoke(initial_state)  # type: ignore[attr-defined]
    return result  # type: ignore[no-any-return]
