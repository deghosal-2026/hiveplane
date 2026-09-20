"""Demo runner (F-86, D11 §5): `plancritic demo` — the end-to-end story run.

The runner drives the *real* engine loop, the *real* re-gate, and the *real*
replan against a corpus goal, with scripted (hermetic, zero-LLM) roles. The
flow is deliberate and leaves a full v1 → v2 → v3 lineage in the store:

1. load + validate the corpus goal (fail-closed on shape errors, exit 1),
2. plan the flawed revision 1, then approve the fixed revision 2,
3. execute up to the probe-gated task, flip the demo window env var, and
   let the re-gate report the stale precondition,
4. replan (patch policy) into a version-3 revision with a parent link, and
5. print the five-stage narrative plus replay text and the Mermaid DAG.

:func:`narrative` is a pure function of its inputs so the story is unit-
testable; :func:`run_demo` is the CLI-facing orchestration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from ..engine import Engine
from ..execution import ExecutionRecorder
from ..gates import run_deterministic_gates
from ..loop import LoopConfig
from ..regate import ReGateConfig, ReGateResult, check_preconditions
from ..replan import ReplanAbort, replan
from ..schema.goal import Goal, ReplanPolicy
from ..schema.plan import EnvProbe, PlanVersion, Task
from ..store.base import PlanStore
from ..store.replan_trace import ReplanLink
from ..types import ApprovedPlan, Finding, PlanningError
from ..viz.graph import to_mermaid
from . import DEMO_WINDOW_EXPECTED, DEMO_WINDOW_VAR
from .roles import ScriptedCritic, ScriptedPlanner


def narrative(
    *,
    goal: Goal,
    v1: PlanVersion,
    v1_findings: list[Finding],
    approved: ApprovedPlan,
    re_gate: ReGateResult | None,
    replanned: PlanVersion | None = None,
    replan_abort: ReplanAbort | None = None,
) -> list[str]:
    """The ordered five-stage narrative lines for the demo.

    Args:
        goal: The corpus goal the demo ran against.
        v1: The flawed revision 1 (for the "draft" stage).
        v1_findings: Findings the critic produced against revision 1.
        approved: The approved revision 2.
        re_gate: The execution-time re-gate result (None skips the stage).
        replanned: The replan revision, when the re-gate triggered one.
        replan_abort: Surface of :class:`ReplanAbort` under the abort policy.

    Returns:
        The narrative lines, one per stage, in story order.
    """
    lines: list[str] = []
    for finding in v1_findings:
        lines.append(f"[1/5 draft] v{v1.version} {finding.reason_code}: {finding.message}")
    lines.append(f"[2/5 approve] {approved.plan.id} v{approved.plan.version} approved")
    if re_gate is None or re_gate.status == "pass":
        lines.append("[3/5 re-gate] passed (no stale preconditions)")
    else:
        for description in re_gate.stale_preconditions:
            lines.append(f"[3/5 re-gate] stale: {description}")
    if replan_abort is not None:
        lines.append(f"[4/5 replan] aborted: {replan_abort}")
    elif replanned is not None:
        lines.append(
            f"[4/5 replan] {replanned.id} v{replanned.version} (parent {replanned.parent_version})"
        )
    else:
        lines.append("[4/5 replan] none needed")
    lines.append("[5/5 complete] plan -> approve -> re-gate -> replan -> complete")
    return lines


def run_demo(
    goal_path: str | Path,
    store: PlanStore,
    *,
    no_graph: bool = False,
    output_format: Literal["text", "json"] = "text",
) -> int:
    """Run the demo against one corpus goal; return a process exit code.

    Args:
        goal_path: Path to a corpus Goal JSON file.
        store: The plan store to persist the v1 → v2 → v3 lineage into.
        no_graph: Skip replay text + Mermaid rendering (plain narrative only).
        output_format: ``text`` prints the human narrative (D11 §6);
            ``json`` prints the structured :func:`demo_payload` (C20).

    Returns:
        0 on a completed narrative; 1 when the goal is invalid or planning
        did not converge (fail-closed, D11 §8).
    """
    path = Path(goal_path)
    try:
        goal = Goal.model_validate(json.loads(path.read_text()))
    except Exception:
        print(f"demo failed: {goal_path} is not a valid Goal")
        return 1

    planner = ScriptedPlanner()
    critic = ScriptedCritic()

    # Revision 1: the flawed draft, as the loop will hand it to the critic.
    try:
        v1 = planner.decompose(goal)
    except PlanningError as err:
        print(f"demo failed: {err}")
        return 1
    v1_findings = list(run_deterministic_gates(v1)) + list(critic.audit(v1, []))
    store.put_plan_version(v1)
    store.put_findings(v1.id, v1.version, v1_findings)

    # Seed the namespace-scoped demo window so the precondition holds at
    # plan time (D11 §5 step 2); the re-gate step will flip it.
    os.environ.setdefault(DEMO_WINDOW_VAR, DEMO_WINDOW_EXPECTED)

    result = Engine(planner=planner, critic=critic, config=LoopConfig()).plan(goal)
    approved = result.approved_plan
    if not result.is_approved or approved is None:
        print(f"demo failed: planning did not converge ({result.reason_code})")
        return 1
    store.put_plan_version(approved.plan)
    store.put_findings(approved.plan.id, approved.plan.version, result.findings)

    # Execute up to the probe-gated task, then flip the window and re-gate.
    recorder = ExecutionRecorder(store)
    drift_task = _drift_task(approved)
    re_gate = _execute_and_regate(approved, drift_task, store, recorder)

    # Replan under the goal's policy (patch/restart -> new version; abort -> stop).
    replanned: PlanVersion | None = None
    replan_abort: ReplanAbort | None = None
    if re_gate is not None and re_gate.status == "stale":
        revised = planner.revise(approved.plan, [])
        try:
            replanned = replan(goal, approved.plan, revised)
        except ReplanAbort as err:
            replan_abort = err
        else:
            store.put_plan_version(replanned)
            store.put_replan_link(
                ReplanLink(
                    plan_id=replanned.id,
                    version=replanned.version,
                    parent_plan_id=approved.plan.id,
                    parent_version=approved.plan.version,
                    policy=_link_policy(goal),
                )
            )

    narrative_lines = narrative(
        goal=goal,
        v1=v1,
        v1_findings=v1_findings,
        approved=approved,
        re_gate=re_gate,
        replanned=replanned,
        replan_abort=replan_abort,
    )
    replay_lines: list[str] | None = None
    graph: str | None = None
    if not no_graph:
        replay_lines = _render_replay(store, goal.id)
        graph = to_mermaid(approved.plan)

    payload = demo_payload(
        goal=goal,
        v1=v1,
        v1_findings=v1_findings,
        approved=approved,
        re_gate=re_gate,
        replanned=replanned,
        replan_abort=replan_abort,
        replay_lines=replay_lines,
        graph=graph,
    )
    if output_format == "json":
        print(json.dumps(payload, indent=2))
        return 0

    for line in narrative_lines:
        print(line)
    if not no_graph and replay_lines is not None and graph is not None:
        for line in replay_lines:
            print(line)
        print(graph, end="")
    return 0


def demo_payload(
    *,
    goal: Goal,
    v1: PlanVersion,
    v1_findings: list[Finding],
    approved: ApprovedPlan,
    re_gate: ReGateResult | None,
    replanned: PlanVersion | None = None,
    replan_abort: ReplanAbort | None = None,
    replay_lines: list[str] | None = None,
    graph: str | None = None,
) -> dict[str, Any]:
    """Structured, machine-readable summary of a completed demo story.

    C20 of the 0.2.0 field test requires ``plancritic demo --format json`` to
    emit a machine-readable record (not just the text narrative) so the demo
    can be consumed by CI or reports. This function collects every stage of
    the story — the seeded draft, approval, re-gate verdict, and (optional)
    replan — into a stable JSON shape.

    Args:
        goal: The corpus goal the demo ran against.
        v1: The flawed revision 1.
        v1_findings: Findings the critic produced against revision 1.
        approved: The approved revision 2.
        re_gate: The execution-time re-gate result.
        replanned: The replan revision, when the re-gate triggered one.
        replan_abort: Surface of :class:`ReplanAbort` under the abort policy.
        replay_lines: Per-revision ``plancritic replay`` lines (info).
        graph: The Mermaid DAG (info; excluded when ``--no-graph``).

    Returns:
        A JSON-serializable payload describing the demo run.
    """
    re_gate_status = "pass" if re_gate is None or re_gate.status == "pass" else "stale"
    replanned_ref = (
        None
        if replanned is None
        else {
            "id": replanned.id,
            "version": replanned.version,
            "parent_version": replanned.parent_version,
        }
    )
    if replan_abort is not None:
        replan: dict[str, Any] | None = {"aborted": str(replan_abort)}
    else:
        replan = None if replanned is None else {"plan": replanned_ref}
    return {
        "goal": goal.id,
        "risk_tolerance": goal.risk_tolerance.value,
        "replan_policy": goal.replan_policy.value,
        "draft": {
            "plan_id": v1.id,
            "version": v1.version,
            "tasks": len(v1.tasks),
            "findings": [
                {"reason_code": f.reason_code, "severity": f.severity.value, "message": f.message}
                for f in v1_findings
            ],
        },
        "approved": {
            "plan_id": approved.plan.id,
            "version": approved.plan.version,
            "tasks": len(approved.plan.tasks),
        },
        "re_gate": {"status": re_gate_status},
        "replan": replan,
        "replay": replay_lines,
        "graph": graph,
    }


def _link_policy(goal: Goal) -> Literal["patch", "restart"]:
    """The store's replan-link policy for a goal (abort never reaches here)."""
    if goal.replan_policy is ReplanPolicy.RESTART:
        return "restart"
    return "patch"


def _drift_task(approved: ApprovedPlan) -> Task | None:
    """The first task with a probe-gated precondition, if any (D11 §5 step 4)."""
    for task in approved.plan.tasks:
        if any(precondition.probe is not None for precondition in task.preconditions):
            return task
    return None


def _execute_and_regate(
    approved: ApprovedPlan,
    drift_task: Task | None,
    store: PlanStore,
    recorder: ExecutionRecorder,
) -> ReGateResult:
    """Execute the pre-drift tasks, then re-gate the drifted one.

    Args:
        approved: The approved plan being executed.
        drift_task: The probe-gated task to re-gate, or None to skip.
        store: Backing store for the re-gate check.
        recorder: Execution recorder for the pre-drift trace.

    Returns:
        The re-gate result ("stale" when the flipped precondition fails).
    """
    if drift_task is None:
        return ReGateResult(status="pass")

    for task in approved.plan.tasks:
        if task.id == drift_task.id:
            break
        recorder.record(approved, task.id, "ok")

    probe = next(
        precondition.probe
        for precondition in drift_task.preconditions
        if precondition.probe is not None
    )
    result = _regate_after_drift(approved, drift_task.id, store, probe)
    recorder.record(
        approved,
        drift_task.id,
        "blocked: re-gate stale" if result.status == "stale" else "ok",
    )
    return result


def _regate_after_drift(
    approved: ApprovedPlan,
    task_id: str,
    store: PlanStore,
    probe: EnvProbe,
) -> ReGateResult:
    """Flip the probe's env var, re-gate, and always restore the old value."""
    previous = os.environ.get(probe.query)
    try:
        os.environ[probe.query] = f"not-{probe.expected}"
        return check_preconditions(approved, task_id, store, ReGateConfig(mode="before-each-step"))
    finally:
        if previous is None:
            os.environ.pop(probe.query, None)
        else:
            os.environ[probe.query] = previous


def _render_replay(store: PlanStore, goal_id: str) -> list[str]:
    """One line per stored revision, mirroring ``plancritic replay`` text."""
    plans = sorted(
        (plan for plan in store.list_plans(goal_id=goal_id)),
        key=lambda plan: plan.version,
    )
    lines = ["replay:"]
    for plan in plans:
        findings = _findings_for(store, plan.id, plan.version)
        lines.append(f"    v{plan.version}: {len(plan.tasks)} tasks, {len(findings)} findings")
        for finding in findings:
            lines.append(
                f"        [{finding.severity.value}] {finding.reason_code}: {finding.message}"
            )
    return lines


def _findings_for(store: PlanStore, plan_id: str, version: int) -> list[Finding]:
    """Fetch findings for a revision (same index trick as ``viz/replay``)."""
    index = getattr(store, "_findings", None)
    if index is not None:
        return list(index.get((plan_id, version), []))
    return []


__all__ = ["demo_payload", "narrative", "run_demo"]
