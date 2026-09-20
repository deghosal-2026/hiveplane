"""
judge_graph.py — an agentic LLM-judge with escalation, built on LangGraph.

The story: a naive LLM-judge scores code by *reading* it and can be fooled by
clean-but-wrong solutions. This graph adds a control loop around the judge:

    run_tests (ground truth) -> judge -> router
        - judge agrees with tests & is confident      -> finalize -> END
        - judge contradicts tests OR is unsure         -> escalate (reveal the
                                                          test results, re-judge)  ┐
                                                                                   │ cycle
        - still unresolved after escalation            -> human_review (interrupt) ┘

It demonstrates the four LangGraph features interviewers ask about:
  * StateGraph with a typed, accumulating state
  * conditional edges (routing on judge-vs-ground-truth)
  * cycles (escalate -> judge loop)
  * human-in-the-loop via interrupt() + a checkpointer

The judge is swappable: a free, offline MOCK by default; a real Anthropic judge
if you set EVAL_GRAPH_REAL=1 (uses ANTHROPIC_API_KEY).

Run:  python judge_graph.py
"""

from __future__ import annotations

import operator
import os
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command


# --------------------------------------------------------------------------
# Tasks + a tiny deterministic test runner (the objective ground truth)
# --------------------------------------------------------------------------
TASKS = {
    "clamp": {
        "func": "clamp",
        "prompt": "def clamp(x, lo, hi): return x limited to the range [lo, hi].",
        "cases": [
            ((5, 0, 10), 5),
            ((-3, 0, 10), 0),
            ((99, 0, 10), 10),
            ((7, 1, 5), 5),
            ((2, 1, 5), 2),
        ],
    },
}


def run_task_tests(task_id: str, solution: str) -> tuple[int, int]:
    """Exec the candidate solution and score it against the task's cases."""
    task = TASKS[task_id]
    total = len(task["cases"])
    ns: dict = {}
    try:
        exec(solution, ns)
        fn = ns[task["func"]]
    except Exception:
        return 0, total
    passed = 0
    for args, expected in task["cases"]:
        try:
            if fn(*args) == expected:
                passed += 1
        except Exception:
            pass
    return passed, total


# --------------------------------------------------------------------------
# The judge (mock by default; real Anthropic if EVAL_GRAPH_REAL=1)
# --------------------------------------------------------------------------
def mock_judge(solution: str, reveal_tests: bool, passed: int, total: int) -> tuple[int, float]:
    """A deliberately naive judge, so the control loop has something to catch.

    - Blind first pass: rewards clean-LOOKING code and ignores correctness
      (this is the failure mode real LLM-judges have).
    - When the escalation step REVEALS the unit-test results, it corrects itself.
    - A solution tagged '# ambiguous' is one it can never score confidently ->
      forces the human-in-the-loop path.
    """
    if "# ambiguous" in solution:
        return 3, 0.5
    if reveal_tests:
        return (2, 0.9) if passed < total else (5, 0.9)
    looks_clean = ("def " in solution) and ("return" in solution) and ("TODO" not in solution)
    if looks_clean:
        return 5, 0.7  # confident but naive: it's blind to correctness
    return 3, 0.5


def real_judge(prompt: str, solution: str, reveal_tests: bool, passed: int, total: int) -> tuple[int, float]:
    """Score with Claude. Only used when EVAL_GRAPH_REAL=1 and the SDK+key exist."""
    import json
    import anthropic

    ground_truth = ""
    if reveal_tests:
        ground_truth = f"\n\nNOTE: this solution passed {passed}/{total} hidden unit tests."
    msg = (
        "You are a strict code judge. Score the solution's correctness 1-5 and give a "
        "confidence 0-1. Reply with ONLY JSON: {\"score\": int, \"confidence\": float}.\n\n"
        f"Task: {prompt}\n\nSolution:\n```python\n{solution}\n```{ground_truth}"
    )
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": msg}],
    )
    data = json.loads(resp.content[0].text.strip().strip("`"))
    return int(data["score"]), float(data["confidence"])


def score_solution(state: "EvalState") -> tuple[int, float]:
    use_real = os.environ.get("EVAL_GRAPH_REAL") == "1"
    if use_real:
        try:
            return real_judge(state["prompt"], state["solution"], state["reveal_tests"],
                              state["tests_passed"], state["tests_total"])
        except Exception as e:  # missing SDK/key/parse -> fall back, stay runnable
            print(f"   (real judge unavailable: {e}; using mock)")
    return mock_judge(state["solution"], state["reveal_tests"],
                      state["tests_passed"], state["tests_total"])


# --------------------------------------------------------------------------
# Graph state
# --------------------------------------------------------------------------
class EvalState(TypedDict):
    task_id: str
    prompt: str
    solution: str
    tests_passed: int
    tests_total: int
    judge_score: int
    judge_confidence: float
    reveal_tests: bool
    attempts: int
    verdict: str
    log: Annotated[list[str], operator.add]  # reducer: nodes append to the log


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------
def run_tests_node(state: EvalState) -> dict:
    passed, total = run_task_tests(state["task_id"], state["solution"])
    return {"tests_passed": passed, "tests_total": total,
            "log": [f"run_tests -> {passed}/{total} passed"]}


def judge_node(state: EvalState) -> dict:
    score, conf = score_solution(state)
    reveal = state["reveal_tests"]
    return {"judge_score": score, "judge_confidence": conf,
            "log": [f"judge(reveal_tests={reveal}) -> score={score}, confidence={conf}"]}


def escalate_node(state: EvalState) -> dict:
    return {"reveal_tests": True, "attempts": state["attempts"] + 1,
            "log": ["escalate -> revealing unit-test results to the judge, re-judging"]}


def human_review_node(state: EvalState) -> dict:
    decision = interrupt({
        "reason": "Judge could not resolve confidently - human review requested.",
        "task": state["task_id"],
        "judge_score": state["judge_score"],
        "tests": f'{state["tests_passed"]}/{state["tests_total"]}',
    })
    return {"verdict": f"HUMAN:{decision}", "log": [f"human_review -> {decision}"]}


def finalize_node(state: EvalState) -> dict:
    if state.get("verdict", "").startswith("HUMAN"):
        return {"log": ["finalize -> keeping human verdict"]}
    passed = state["tests_passed"] == state["tests_total"]
    verdict = "PASS" if (passed and state["judge_score"] >= 4) else "FAIL"
    return {"verdict": verdict, "log": [f"finalize -> {verdict}"]}


# --------------------------------------------------------------------------
# Router (conditional edge): the heart of the control loop
# --------------------------------------------------------------------------
def route(state: EvalState) -> str:
    contradiction = state["judge_score"] >= 4 and state["tests_passed"] < state["tests_total"]
    unsure = state["judge_confidence"] < 0.6
    if (contradiction or unsure) and state["attempts"] < 2:
        return "escalate"
    if contradiction or unsure:
        return "human"       # escalation didn't resolve it
    return "finalize"


# --------------------------------------------------------------------------
# Build + compile the graph
# --------------------------------------------------------------------------
def build_graph():
    b = StateGraph(EvalState)
    b.add_node("run_tests", run_tests_node)
    b.add_node("judge", judge_node)
    b.add_node("escalate", escalate_node)
    b.add_node("human_review", human_review_node)
    b.add_node("finalize", finalize_node)

    b.add_edge(START, "run_tests")
    b.add_edge("run_tests", "judge")
    b.add_conditional_edges("judge", route,
                            {"escalate": "escalate", "human": "human_review", "finalize": "finalize"})
    b.add_edge("escalate", "judge")      # <-- the cycle
    b.add_edge("human_review", "finalize")
    b.add_edge("finalize", END)

    return b.compile(checkpointer=MemorySaver())  # checkpointer enables interrupt/resume


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------
SAMPLES = {
    "correct":  "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
    "buggy":    "def clamp(x, lo, hi):\n    return min(lo, max(hi, x))\n",       # clean-looking, WRONG
    "ambiguous":"def clamp(x, lo, hi):  # ambiguous\n    return sorted([lo, x, hi])[1]\n",
}


def _initial(task_id: str, solution: str) -> EvalState:
    return {"task_id": task_id, "prompt": TASKS[task_id]["prompt"], "solution": solution,
            "tests_passed": 0, "tests_total": 0, "judge_score": 0, "judge_confidence": 0.0,
            "reveal_tests": False, "attempts": 0, "verdict": "", "log": []}


def run_one(graph, label: str, solution: str):
    print(f"\n=== {label} solution ===")
    cfg = {"configurable": {"thread_id": f"eval-{label}"}}
    result = graph.invoke(_initial("clamp", solution), cfg)
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"   [interrupt] {payload['reason']}  (judge={payload['judge_score']}, tests={payload['tests']})")
        print("   [human] overriding -> FAIL")
        result = graph.invoke(Command(resume="FAIL"), cfg)   # resume the paused graph
    for line in result["log"]:
        print(f"   - {line}")
    print(f"   VERDICT: {result['verdict']}")


if __name__ == "__main__":
    graph = build_graph()
    print("Agentic judge-escalation eval graph (LangGraph). Judge = "
          + ("REAL Anthropic" if os.environ.get("EVAL_GRAPH_REAL") == "1" else "mock/offline"))
    run_one(graph, "correct", SAMPLES["correct"])
    run_one(graph, "buggy", SAMPLES["buggy"])
    run_one(graph, "ambiguous", SAMPLES["ambiguous"])
