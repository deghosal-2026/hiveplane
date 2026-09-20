from __future__ import annotations
import logging
import os
import sqlite3
import uuid
from typing import Sequence

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from state import AgentState
from nodes.fetch import fetch_repo_data
from nodes.supervisor import router_supervisor, aggregate_sub_results
from nodes.classify import classify_sub
from nodes.breaking import breaking_sub
from nodes.security import security_sub
from nodes.interrupt import interrupt_breaking, interrupt_security, interrupt_semver
from nodes.resolve import resolve_breaking, resolve_security
from nodes.tone import retrieve_tone
from nodes.draft import draft_notes
from nodes.semver import propose_semver_node
from nodes.output import write_output
from nodes.audit import fetch_releases_node, audit_releases_node, print_report_node

logger = logging.getLogger(__name__)


def route_to_sub_agents(state: AgentState) -> Sequence[str]:
    pending = state.get("pending_routing", [])
    agents = set(p["sub_agent"] for p in pending)
    return list(agents) if agents else ["classify_sub"]


def route_after_aggregation(state: AgentState) -> str:
    breaking = state.get("breaking_changes", [])
    security = state.get("security_fixes", [])
    if breaking:
        return "interrupt_breaking"
    if security:
        return "interrupt_security"
    return "retrieve_tone"


def route_breaking_resume(state: AgentState) -> str:
    breaking = state.get("breaking_changes", [])
    index = state.get("current_interrupt_index", 0)
    if index < len(breaking):
        return "interrupt_breaking"
    if state.get("security_fixes", []):
        return "interrupt_security"
    return "retrieve_tone"


def route_security_resume(state: AgentState) -> str:
    return "retrieve_tone"


def route_semver_approval(state: AgentState) -> str:
    if state.get("semver_cancelled"):
        return "write_output"
    if state.get("semver_approved"):
        return "write_output"
    if state.get("semver_override"):
        return "propose_semver"
    return "write_output"


def build_graph(mode: str = "generate", checkpointer=None):
    graph = StateGraph(AgentState)

    graph.add_node("fetch_repo_data", fetch_repo_data)
    graph.add_node("router_supervisor", router_supervisor)
    graph.add_node("classify_sub", classify_sub)
    graph.add_node("breaking_sub", breaking_sub)
    graph.add_node("security_sub", security_sub)
    graph.add_node("aggregate_sub_results", aggregate_sub_results)
    graph.add_node("interrupt_breaking", interrupt_breaking)
    graph.add_node("resolve_breaking", resolve_breaking)
    graph.add_node("interrupt_security", interrupt_security)
    graph.add_node("resolve_security", resolve_security)

    graph.add_node("retrieve_tone", retrieve_tone)
    graph.add_node("draft_notes", draft_notes)
    graph.add_node("propose_semver", propose_semver_node)
    graph.add_node("interrupt_semver", interrupt_semver)
    graph.add_node("write_output", write_output)
    graph.add_node("checkpoint_output", write_output)

    graph.add_edge("write_output", END)

    graph.add_node("fetch_releases", fetch_releases_node)
    graph.add_node("audit_releases", audit_releases_node)
    graph.add_node("print_report", print_report_node)

    graph.add_edge("print_report", END)

    if mode == "audit":
        graph.set_entry_point("fetch_releases")
        graph.add_edge("fetch_releases", "audit_releases")
        graph.add_edge("audit_releases", "print_report")
    else:
        graph.set_entry_point("fetch_repo_data")
        graph.add_edge("fetch_repo_data", "router_supervisor")
        graph.add_conditional_edges(
            "router_supervisor",
            route_to_sub_agents,
            {
                "classify_sub": "classify_sub",
                "breaking_sub": "breaking_sub",
                "security_sub": "security_sub",
            },
        )

        graph.add_edge("classify_sub", "aggregate_sub_results")
        graph.add_edge("breaking_sub", "aggregate_sub_results")
        graph.add_edge("security_sub", "aggregate_sub_results")

        graph.add_conditional_edges(
            "aggregate_sub_results",
            route_after_aggregation,
            {
                "interrupt_breaking": "interrupt_breaking",
                "interrupt_security": "interrupt_security",
                "retrieve_tone": "retrieve_tone",
            },
        )

        graph.add_edge("interrupt_breaking", "resolve_breaking")
        graph.add_conditional_edges(
            "resolve_breaking",
            route_breaking_resume,
            {
                "interrupt_breaking": "interrupt_breaking",
                "interrupt_security": "interrupt_security",
                "retrieve_tone": "retrieve_tone",
            },
        )

        graph.add_edge("interrupt_security", "resolve_security")
        graph.add_conditional_edges(
            "resolve_security",
            route_security_resume,
            {
                "interrupt_security": "interrupt_security",
                "retrieve_tone": "retrieve_tone",
            },
        )

        graph.add_edge("retrieve_tone", "draft_notes")
        graph.add_edge("draft_notes", "checkpoint_output")
        graph.add_edge("checkpoint_output", "propose_semver")
        graph.add_edge("propose_semver", "interrupt_semver")
        graph.add_conditional_edges(
            "interrupt_semver",
            route_semver_approval,
            {
                "write_output": "write_output",
                "propose_semver": "propose_semver",
            },
        )

    if checkpointer is None:
        os.makedirs("logs", exist_ok=True)
        conn = sqlite3.connect("logs/checkpoints.db", check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    return graph.compile(checkpointer=checkpointer)


def print_thread_id() -> str:
    thread_id = str(uuid.uuid4())
    logger.info("Session ID: %s", thread_id)
    print(f"Session ID: {thread_id}", flush=True)
    return thread_id
