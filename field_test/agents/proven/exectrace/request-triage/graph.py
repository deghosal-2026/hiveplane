"""LangGraph workflow for the deterministic ``request-triage`` demo agent.

========================================================
Why deterministic / why this shape
========================================================
The demo agent exists to produce **repeatable** tracing fixtures: healthy runs,
runaway loops, and cost-heavy runs. Behavior is entirely a function of the input
request, so the same seed always yields the same category of run. That makes it a
reliable target for the SDK tests, the analytics service, and demo replays.

Graph layout (all edges deterministic):
    START -> planner -> run_tool -+-> resolve -> END
                                  |
                                  +-> escalate -> END
                                  |
                                  +-> planner (retry)

The agent is instrumented by the SDK later (Milestone 2.6 adapter) by wrapping the
``plan_span`` / ``execute_tool_span`` / ``retrieval_span`` helpers around the nodes
that are already separated here -- which is why planning, tool execution, and
routing are each their own node.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from request_triage.tools import (
    ToolResult,
    infer_estimated_cost,
    lookup_account,
    search_kb,
)

# How many planner turns a run may take before it is forced to escalate. This also
# bounds the cost of a seeded run so analytics fixtures stay small and cheap, and
# guarantees the "loop" scenario eventually terminates (no infinite retry).
MAX_STEPS = 12

# Fixed, deterministic per-turn cost used by the demo's best-effort cost model.
# Chosen so high-cost runs clearly stand apart from normal runs in cost analytics
# while staying trivially small to read in traces.
PER_TURN_COST = 0.01


class TriageState(TypedDict, total=False):
    """Execution state threaded through the LangGraph nodes.

    Declared ``total=False`` so nodes may return partial updates (LangGraph merges
    returned dicts into shared state). Fields:

    * ``scenario`` / ``intent`` / ``account_id``: input seeded by the run driver.
    * ``step``: planner turn counter; drives escalation and cost.
    * ``plan`` / ``last_tool``: what the planner decided and what ran last.
    * ``kb_hit`` / ``account_ok``: **evidence** the resolution gate reads.
    * ``outcome`` / ``status``: terminal decision and its success flag.
    * ``estimated_cost``: best-effort deterministic cost estimate.
    * ``agent_version``: version label attached to the run.
    * ``tool_log``: ordered list of executed tool names (a compact run history that
      the run-timeline view can render without re-walking spans).
    """

    scenario: str
    intent: str
    account_id: str
    step: int
    plan: str
    last_tool: str
    kb_hit: str | None
    account_ok: bool
    outcome: str
    status: str
    estimated_cost: float
    agent_version: str
    tool_log: list[str]


def _intent_from(state: TriageState) -> str:
    """Map the seeded intent to a resolution path.

    Normalization is explicit (strip + lowercase) so a seed never accidentally
    misses the knowledge base due to casing. Returns one of ``"normal"``,
    ``"missing_account"``, or ``"open_ended"`` -- the three behavior classes the
    demo needs.
    """
    intent = state.get("intent", "").strip().lower()
    if intent == "open_ended":
        return "open_ended"
    if intent in {"missing_account"}:
        return "missing_account"
    return "normal"


def planner(state: TriageState) -> TriageState:
    """Decide the next action deterministically from the current step.

    The chosen plan is driven by tool results accumulated in state: the agent only
    moves toward resolution after it has both checked the account and found an
    answer, and it escalates only when it runs out of steps.

    Scenarios:
    * ``normal``: lookup the account once, then search the KB -> converges quickly.
    * ``missing_account``: alternating lookups (which keep failing) and KB searches
      (which keep missing); never converges until the step cap is hit.
    * ``open_ended``: issue many distinct KB queries across turns; high step count.
    """
    intent = _intent_from(state)
    step = state.get("step", 0)
    next_step = step + 1
    outcome = ""
    plan = ""

    # Step cap is checked first: once exceeded, the run escalates regardless of
    # intent. The plan is cleared so the next ``run_tool`` turn has nothing to do.
    if next_step > MAX_STEPS:
        outcome = "escalate"
    elif intent == "normal":
        plan = "lookup_account" if step == 0 else "search_kb"
    elif intent == "missing_account":
        # Alternate lookups that keep failing and knowledge searches that keep
        # missing; the agent never converges until the step cap is hit.
        plan = "lookup_account" if step % 2 == 0 else "search_kb"
    else:  # open_ended / high-cost
        # Exploratory: issue many distinct knowledge-base queries across turns.
        plan = "search_kb"

    return {
        "step": next_step,
        "plan": plan if not outcome else "",
        "outcome": outcome,
    }


def run_tool(state: TriageState) -> TriageState:
    """Execute the current plan's tool and resolve only on real success.

    Resolution is gated on evidence from the tools themselves: an answer is only
    accepted when a knowledge-base hit exists AND the account is known good. A
    missing account therefore forces the run to keep trying until escalation --
    which is exactly the loop the analytics service is meant to detect in traces.
    """
    plan = state.get("plan", "")
    if plan == "":
        # Terminal escalation step has nothing to execute; carry the outcome through.
        return {"outcome": state.get("outcome", "escalate")}

    result: ToolResult

    if plan == "search_kb":
        result = search_kb(state.get("intent", ""))
    elif plan == "lookup_account":
        result = lookup_account(state.get("account_id", ""))
    else:
        result = ToolResult(message="no-op")

    # Append the executed tool to the evidence trail (immutable-style list update
    # so LangGraph state merging keeps prior entries).
    tool_log = state.get("tool_log", [])
    if plan in {"search_kb", "lookup_account"}:
        tool_log = [*tool_log, plan]

    # Evidence accumulates: account status is only refreshed when this turn looked
    # the account up; KB hit likewise. Each gate below is therefore the *latest*
    # known value, not a per-turn reset.
    account_ok = result.ok if plan == "lookup_account" else state.get("account_ok", False)
    kb_hit = result.hit if plan == "search_kb" else state.get("kb_hit")

    # Resolution requires BOTH evidence gates to hold. This is the tool-outcome
    # driven design: no resolution without a real KB hit AND a good account.
    outcome = ""
    status = ""
    if kb_hit is not None and account_ok:
        outcome = "resolve"
        status = "ok"

    return {
        "last_tool": plan,
        "kb_hit": kb_hit,
        "account_ok": account_ok,
        "tool_log": tool_log,
        "outcome": outcome,
        "status": status,
        "estimated_cost": infer_estimated_cost(state.get("step", 0), PER_TURN_COST),
    }


def route(state: TriageState) -> str:
    """Route to the next node based on the last plan and outcome.

    Ordering matters: a resolved or escalated run must exit the planner loop
    immediately; anything else retries (back to ``planner``).
    """
    if state.get("outcome") == "resolve":
        return "resolve"
    if state.get("outcome") == "escalate":
        return "escalate"
    return "planner"


def resolve(state: TriageState) -> TriageState:
    """Terminal outcome for a successfully resolved request."""
    return {"outcome": "resolve", "status": "ok"}


def escalate(state: TriageState) -> TriageState:
    """Terminal outcome when the agent gives up or hits the step cap."""
    return {"outcome": "escalate", "status": "error"}


def build_graph() -> CompiledStateGraph[TriageState, None, TriageState, TriageState]:
    """Construct the compiled request-triage LangGraph.

    Node names are stable identifiers (the LangGraph adapter wraps instrumentation
    around them by name). The explicit four-type annotation matches LangGraph's
    ``CompiledStateGraph`` generic signature so mypy ``--strict`` stays happy.
    """
    g = StateGraph(TriageState)
    g.add_node("planner", planner)
    g.add_node("run_tool", run_tool)
    g.add_node("resolve", resolve)
    g.add_node("escalate", escalate)
    g.add_edge(START, "planner")
    g.add_edge("planner", "run_tool")
    g.add_conditional_edges("run_tool", route, ["planner", "resolve", "escalate"])
    g.add_edge("resolve", END)
    g.add_edge("escalate", END)
    return g.compile()


DEFAULT_VERSION = "v0.1.0"
