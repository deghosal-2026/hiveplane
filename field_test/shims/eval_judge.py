"""HivePlane LangGraph shim over the downloaded exectrace judge graph.

Wraps the deterministic agentic judge (``judge_graph.py``) so it runs under
the control-plane LangGraph adapter: an init node flattens the submitted task
and reads the issue through the tool boundary, then the graph reuses the real
judge nodes verbatim — including the ``interrupt()`` human-review gate, which
maps to a paused run and resumes with ``Command(resume=True)``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from hiveplane.checkpointing import default_checkpointer

_ROOT = Path(__file__).resolve().parents[2]
_EVAL_GRAPH_ROOT = _ROOT / "field_test" / "agents" / "proven" / "exectrace" / "agent-eval-graph"
if str(_EVAL_GRAPH_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_GRAPH_ROOT))

import judge_graph as judge  # type: ignore[import-not-found]


class JudgeState(judge.EvalState, total=False):  # type: ignore[misc, valid-type]
    task: dict[str, Any]


def init_node(state: JudgeState, config: RunnableConfig) -> dict[str, Any]:
    task = state.get("task", {})
    ctx = config.get("configurable", {}).get("hiveplane_ctx")
    if ctx is not None:
        ctx.tool_call("mcp.github.read_issue", host="api.github.com")
    task_id = str(task.get("task_id", "clamp"))
    solution = str(task.get("solution", ""))
    return {
        "task_id": task_id,
        "prompt": judge.TASKS[task_id]["prompt"],
        "solution": solution,
        "tests_passed": 0,
        "tests_total": 0,
        "judge_score": 0,
        "judge_confidence": 0.0,
        "reveal_tests": False,
        "attempts": 0,
        "verdict": "",
        "log": [f"init -> task={task_id}"],
    }


def build() -> Any:
    builder = StateGraph(JudgeState)
    builder.add_node("init", init_node)
    builder.add_node("run_tests", judge.run_tests_node)
    builder.add_node("judge", judge.judge_node)
    builder.add_node("escalate", judge.escalate_node)
    builder.add_node("human_review", judge.human_review_node)
    builder.add_node("finalize", judge.finalize_node)

    builder.add_edge(START, "init")
    builder.add_edge("init", "run_tests")
    builder.add_edge("run_tests", "judge")
    builder.add_conditional_edges(
        "judge",
        judge.route,
        {"escalate": "escalate", "human": "human_review", "finalize": "finalize"},
    )
    builder.add_edge("escalate", "judge")
    builder.add_edge("human_review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=default_checkpointer())


graph = build()