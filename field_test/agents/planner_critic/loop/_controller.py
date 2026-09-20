"""The loop controller (F-05..F-08, F-13) — verbatim §2.6.1 pseudocode.

The controller orchestrates planner, deterministic gates, critic, and the
termination logic. It is **deterministic on identical inputs** (F-74): given
the same planner/critic outputs, the same loop decisions (approve/revise/
escalate) result. CI asserts this via the acceptance matrix
(``tests/fixtures/loop_matrix.yaml``).

```text
function run_loop(goal, planner, critic, config):
    plan_v = planner.decompose(goal)
    for revision in 1..config.revision_cap:
        gates = deterministic_gates(plan_v)             # free, always run
        if critic.mode == "deterministic-first":
            if gates.has_blocker():
                plan_v = planner.revise(plan_v, gates.as_findings()); continue
            findings = gates.as_findings() + critic.audit(plan_v)  # LLM
        else:                                            # llm-every-revision
            findings = gates.as_findings() + critic.audit(plan_v)
        if meets_threshold(findings, goal.risk_tolerance): return Approved(plan_v)
        if budget_exceeded(goal.budget): return Escalate(budget)
        if regression_detected(findings, prior_findings): return Escalate(thrashing)
        if converged(plan_v, prior_plan): return Escalate(stalled)
        prior_plan, prior_findings = plan_v, findings
        plan_v = planner.revise(plan_v, findings)
    return Escalate(revision_cap)
```

M1 keeps the loop model-agnostic (fake roles in tests, real ones later) and
store-less. Escalation surfaces a precise question so the escalation manager
can present it to a human.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

from ..approval import ApprovalGate, meets_threshold
from ..critique.diff import scope_between
from ..critique.mode import CriticMode, should_invoke_llm
from ..gates import run_deterministic_gates
from ..gates.base import BaseGate
from ..ledger import PreconditionLedger
from ..reason_codes import (
    CRITIC_SATISFIED,
    RUN_BUDGET_EXCEEDED,
    ReasonCode,
)
from ..redaction import SecretsRedactor
from ..roles import CriticRole, PlannerRole
from ..run_budget import RunBudget
from ..schema.acceptance import AcceptanceContract, bind_acceptance, evaluate_contract
from ..schema.goal import Goal, ReplanPolicy
from ..schema.plan import PlanVersion
from ..types import ApprovedPlan, Escalation, Finding, PlanningError, Severity
from .autofix import apply_ordering_auto_repair, apply_precondition_closer
from .budget import SpendState, budget_exceeded
from .convergence import stalled
from .histogram import FamilyHistogram, compute_family_histogram, detect_histogram_cycling
from .oscillation import compute_plan_signature, detect_oscillation
from .regression import regression_detected

logger = logging.getLogger(__name__)


def _critic_satisfied(findings: list[Finding]) -> bool:
    """Determine if the critic explicitly endorsed the plan.

    A critic is 'satisfied' when it produced no blocker findings and no
    warnings — i.e., it explicitly endorsed the plan as safe to execute.
    This lets strict goals approve when the critic has nothing to object to,
    rather than strictly escalating on any finding.
    """
    return all(
        f.severity is not Severity.BLOCKER and f.severity is not Severity.WARNING for f in findings
    )


@dataclass(frozen=True)
class LoopConfig:
    """Deterministic tuning of the revise-until-approved loop."""

    mode: CriticMode = "deterministic-first"
    revision_cap: int = 3
    adaptive_revision_cap: bool = False
    auto_repair: bool = True
    precondition_closer: bool = True
    oscillation_window: int = 4
    histogram_lag: int = 2
    converge_policy: str = "escalate"

    @staticmethod
    def from_env() -> LoopConfig:
        """Build a LoopConfig from PC_* environment variables.

        Env vars:
            PC_CRITIQUE_MODE: one of heuristic-only|deterministic-first|llm-every-revision
            PC_REVISION_CAP: positive integer (default 3)
            PC_AUTO_REPAIR: on|off (default on)

        Returns:
            A LoopConfig, falling back to defaults on missing/invalid env.
        """
        import os

        from ..critique.mode import validate_mode

        mode_str = os.environ.get("PC_CRITIQUE_MODE", "deterministic-first")
        try:
            mode = validate_mode(mode_str)
        except ValueError:
            logger.warning("invalid PC_CRITIQUE_MODE=%r, using default", mode_str)
            mode = "deterministic-first"

        cap_str = os.environ.get("PC_REVISION_CAP", "3")
        try:
            cap = max(1, int(cap_str))
        except ValueError:
            logger.warning("invalid PC_REVISION_CAP=%r, using default", cap_str)
            cap = 3

        repair_str = os.environ.get("PC_AUTO_REPAIR", "on").strip().lower()
        auto_repair = repair_str in ("on", "1", "true", "yes")

        closer_str = os.environ.get("PC_PRECONDITION_CLOSER", "on").strip().lower()
        precondition_closer = closer_str in ("on", "1", "true", "yes")

        window_str = os.environ.get("PC_OSCILLATION_WINDOW", "")
        try:
            oscillation_window = max(2, int(window_str)) if window_str else 4
        except ValueError:
            oscillation_window = 4

        lag_str = os.environ.get("PC_HISTOGRAM_LAG", "")
        try:
            histogram_lag = max(2, int(lag_str)) if lag_str else 2
        except ValueError:
            histogram_lag = 2

        converge_str = os.environ.get("PC_CONVERGE_POLICY", "escalate").strip().lower()
        converge_policy = (
            converge_str if converge_str in ("escalate", "auto_converge") else "escalate"
        )

        return LoopConfig(
            mode=mode,
            revision_cap=cap,
            auto_repair=auto_repair,
            precondition_closer=precondition_closer,
            oscillation_window=oscillation_window,
            histogram_lag=histogram_lag,
            converge_policy=converge_policy,
        )


@dataclass
class LoopResult:
    """The consolidated outcome of one ``run_loop`` invocation."""

    status: Literal["approved", "escalated"]
    plan: PlanVersion | None = None
    findings: list[Finding] = field(default_factory=list)
    reason_code: ReasonCode | None = None
    approved_plan: ApprovedPlan | None = None
    escalation: Escalation | None = None
    spend: SpendState | None = None
    mode: Literal["live", "shadow"] = "live"

    @property
    def is_approved(self) -> bool:
        """True when the loop produced an ApprovedPlan."""
        return self.status == "approved"


def run_loop(
    goal: Goal,
    planner: PlannerRole,
    critic: CriticRole,
    config: LoopConfig | None = None,
    spend: SpendState | None = None,
    extra_gates: list[BaseGate] | None = None,
    run_budget: RunBudget | None = None,
    precondition_ledger: PreconditionLedger | None = None,
    redactor: SecretsRedactor | None = None,
    acceptance: AcceptanceContract | None = None,
) -> LoopResult:
    """Run the draft → critique → revise → (approve|escalate) loop.

    Args:
        goal: The typed planning request.
        planner: Role that decomposes/revises plans.
        critic: Role that audits plans (returns findings).
        config: Loop tuning (mode + revision cap). Defaults to
            deterministic-first with cap 3.
        spend: Optional spend counter; a fresh one is created when omitted.
        extra_gates: Optional domain-pack gate evaluators to run *in
            addition* to the built-in six.
        run_budget: Optional run-level budget ceilings enforced above the
            per-goal F-13 budget.
        precondition_ledger: Optional deterministic precondition state store
            that survives context compaction.
        redactor: Optional secrets redactor applied to output surfaces.

    Returns:
        A :class:`LoopResult` — ``approved`` (with an ``ApprovedPlan``) or
        ``escalated`` (with a precise question and reason code).

    Raises:
        PlanningError: When the planner fails to produce a valid plan with a
            failure reason (fail-closed; never continues on garbage).
    """
    cfg = config or LoopConfig()
    state = spend or SpendState()

    result = _run(
        goal=goal,
        planner=planner,
        critic=critic,
        config=cfg,
        state=state,
        extra_gates=extra_gates,
        run_budget=run_budget,
        precondition_ledger=precondition_ledger,
        redactor=redactor,
        acceptance=acceptance,
    )
    result.spend = state
    return result


def _escalate(
    goal: Goal,
    plan: PlanVersion,
    findings: list[Finding],
    reason: ReasonCode,
    index: int,
) -> LoopResult:
    """Build an escalation result with a precise question (M1 form)."""
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    question = _compose_question(goal, blockers, reason)
    escalation = Escalation(
        id=f"escalation:{goal.id}:{plan.version}:{index}",
        plan_id=plan.id,
        version=plan.version,
        blocker_finding_id=blockers[0].id if blockers else None,
        question=question,
    )
    return LoopResult(
        status="escalated",
        plan=plan,
        findings=findings,
        reason_code=reason,
        escalation=escalation,
    )


def _build_auto_converged_plan(
    current_plan: PlanVersion, prior_plan: PlanVersion | None
) -> PlanVersion | None:
    """Merge non-oscillating tasks into a single stable plan.

    When the loop detects structural oscillation, this function builds a
    plan containing only the tasks that are **stable** (appear with the same
    structural properties across the oscillating revisions). Tasks that
    differ across oscillation shapes are excluded.

    Args:
        current_plan: The current plan revision (one of the oscillating shapes).
        prior_plan: The prior revision to compare against.

    Returns:
        A new :class:`PlanVersion` with only stable tasks, or None when a
        stable subset cannot be determined.
    """
    if prior_plan is None:
        return None
    from .oscillation import oscillating_task_ids

    oscillating = oscillating_task_ids(prior_plan, current_plan)
    if not oscillating:
        return None

    stable_tasks = [t for t in current_plan.tasks if t.id not in oscillating]
    if not stable_tasks:
        return None

    stable_ids = {t.id for t in stable_tasks}
    stable_deps = [
        d
        for d in current_plan.dependencies
        if d.from_task in stable_ids and d.to_task in stable_ids
    ]
    return current_plan.model_copy(
        update={
            "tasks": stable_tasks,
            "dependencies": stable_deps,
        }
    )


def _compose_question(goal: Goal, blockers: list[Finding], reason: ReasonCode) -> str:
    """Human-readable, precise question for the escalation record."""
    if blockers:
        detail = "; ".join(sorted({f.reason_code for f in blockers}))
        return (
            f"Plan for goal {goal.id!r} cannot be approved: blockers remain "
            f"({detail}). Decide whether to patch, override, or abandon."
        )
    return f"Plan for goal {goal.id!r} did not converge ({reason}). Decide next step."


def _plan_revision(plan: PlanVersion, next_id: str, parent: PlanVersion | None) -> PlanVersion:
    """Wrap a replan result as the next immutable revision.

    Args:
        plan: The planner's replan output (usually derived from the parent).
        next_id: Id to stamp on the new revision.
        parent: The parent revision, or None to stamp from scratch.

    Returns:
        The new revision: fresh id/version/parent link and a fresh
        ``created_at`` so each revision carries its own timestamp.
    """

    data = plan.model_dump()
    data["id"] = next_id
    data["version"] = (parent.version + 1) if parent else 1
    data["parent_version"] = parent.id if parent else None
    data["created_at"] = datetime.now(UTC)
    return PlanVersion.model_validate(data)


def _run(
    goal: Goal,
    planner: PlannerRole,
    critic: CriticRole,
    config: LoopConfig,
    state: SpendState,
    extra_gates: list[BaseGate] | None = None,
    run_budget: RunBudget | None = None,
    precondition_ledger: PreconditionLedger | None = None,
    redactor: SecretsRedactor | None = None,
    acceptance: AcceptanceContract | None = None,
) -> LoopResult:
    """Internal deterministic loop body."""
    redactor = redactor or SecretsRedactor()

    logger.info(
        "loop start — goal=%s mode=%s revision_cap=%d risk=%s",
        goal.id,
        config.mode,
        config.revision_cap,
        goal.risk_tolerance,
    )

    # Adaptive revision cap: strict goals always escalate (the LLM critic
    # always finds something), so cap at 1 to save LLM calls
    effective_cap = config.revision_cap
    if config.adaptive_revision_cap and goal.risk_tolerance.value == "strict" and effective_cap > 1:
        effective_cap = 1
        logger.info("loop: strict goal — adaptive cap reduced to 1")
    try:
        logger.info("loop: planner.decompose(goal=%s)", goal.id)
        plan = planner.decompose(goal)
    except Exception as exc:
        logger.error("loop: planner.decompose failed: %s", exc)
        raise PlanningError(f"planner role failed to decompose: {exc}") from exc
    if not isinstance(plan, PlanVersion):
        raise PlanningError(f"planner returned non-PlanVersion: {type(plan).__name__}")

    logger.info(
        "loop: planner produced plan=%s v%d (%d tasks)",
        plan.id,
        plan.version,
        len(plan.tasks),
    )

    if precondition_ledger is not None:
        precondition_ledger.process_plan(plan)
        for diag in precondition_ledger.diagnostics():
            logger.info("ledger: %s", diag.get("message", ""))

    # Bind the acceptance contract before the loop starts (#215): approval
    # reads the frozen posture, never ambient goal/config state.
    contract = acceptance if acceptance is not None else bind_acceptance(goal)

    approval: ApprovalGate = ApprovalGate(contract.risk_tolerance(), goal.approval_ttl)
    prior_plan: PlanVersion | None = None
    prior_findings: list[Finding] = []
    sig_history: list[str] = []
    hist_history: list[FamilyHistogram] = []

    for revision in range(1, effective_cap + 1):
        state.record_revision()
        logger.info(
            "loop: revision %d/%d — plan=%s v%d",
            revision,
            effective_cap,
            plan.id,
            plan.version,
        )

        gate_findings = run_deterministic_gates(plan, extra_gates=extra_gates)
        gate_blockers = [f for f in gate_findings if f.severity is Severity.BLOCKER]
        logger.info(
            "loop: gates — %d findings (%d blockers)",
            len(gate_findings),
            len(gate_blockers),
        )
        accumulated_trace: list[Finding] = []

        if config.auto_repair and gate_blockers:
            repaired_plan, repair_findings = apply_ordering_auto_repair(plan, gate_findings)
            if repaired_plan is not None:
                recheck = run_deterministic_gates(repaired_plan, extra_gates=extra_gates)
                if not _has_blocker(recheck):
                    logger.info("loop: auto-repaired ordering → continue without revision")
                    plan = repaired_plan
                    accumulated_trace.extend(repair_findings)
                    gate_findings = accumulated_trace + recheck
                    gate_blockers = []

        if config.precondition_closer:
            has_unverified = any(f.reason_code == "unverified_precondition" for f in gate_findings)
            if has_unverified:
                closed_plan, close_findings = apply_precondition_closer(plan, gate_findings)
                if closed_plan is not None:
                    logger.info("loop: auto-closed precondition gap → continue without revision")
                    plan = closed_plan
                    recheck = run_deterministic_gates(plan, extra_gates=extra_gates)
                    gate_findings = accumulated_trace + close_findings + recheck
                    gate_blockers = [f for f in recheck if f.severity is Severity.BLOCKER]

        sig_history.append(compute_plan_signature(plan))

        if config.mode == "deterministic-first" and _has_blocker(gate_findings):
            logger.info("loop: gate blocker → revise (no LLM critic spend)")
            if budget_exceeded(goal.constraints.budget, state):
                logger.info("loop: budget exceeded → escalate")
                return _escalate(goal, plan, gate_findings, "budget_exceeded", revision)
            run_budget_hit = run_budget and run_budget.check()
            if run_budget_hit:
                return _escalate(
                    goal,
                    plan,
                    gate_findings,
                    cast("ReasonCode", run_budget_hit),
                    revision,
                )
            if revision < effective_cap:
                plan = _revise_or_raise(
                    planner,
                    plan,
                    gate_findings,
                    next_id=f"plan-{goal.id}-r{revision + 1}",
                )
                continue
            logger.info("loop: revision cap reached → escalate")
            return _escalate(goal, plan, gate_findings, "revision_cap_reached", revision)

        if should_invoke_llm(config.mode, gate_findings):
            if (
                goal.constraints.budget.max_calls is not None
                and state.calls_used >= goal.constraints.budget.max_calls
            ):
                logger.info("loop: LLM call budget exceeded → escalate")
                return _escalate(goal, plan, gate_findings, "budget_exceeded", revision)
            if run_budget and run_budget.check() is not None:
                rc = run_budget.check()
                logger.info("loop: run budget ceiling hit → escalate")
                reason = cast("ReasonCode", rc or RUN_BUDGET_EXCEEDED)
                return _escalate(goal, plan, gate_findings, reason, revision)
            logger.info("loop: invoking LLM critic (call %d)", state.calls_used + 1)
            state.record_llm_call()
            if config.mode == "deterministic-first":
                findings = list(gate_findings) + _safe_audit_diff(
                    critic, plan, gate_findings, prior_plan
                )
            else:
                findings = list(gate_findings) + _safe_audit(critic, plan, gate_findings)
            crit_blockers = [f for f in findings if f.severity is Severity.BLOCKER]
            logger.info(
                "loop: critic — %d total findings (%d blockers)",
                len(findings),
                len(crit_blockers),
            )
        else:
            findings = list(gate_findings)

        threshold_ok, thresholds = evaluate_contract(findings, contract)
        if threshold_ok:
            approved = approval.approve(plan, thresholds)
            logger.info("loop: threshold met → APPROVED plan=%s v%d", plan.id, plan.version)
            return LoopResult(
                status="approved",
                plan=plan,
                findings=findings,
                reason_code="approved",
                approved_plan=approved,
            )

        # Critic satisfaction signal: if the critic explicitly endorsed the plan
        # (no blockers, no warnings), approve even under strict mode
        if _critic_satisfied(findings):
            # Only approve if there are no deterministic gate blockers
            gate_blockers = [f for f in gate_findings if f.severity is Severity.BLOCKER]
            if not gate_blockers:
                approved = approval.approve(plan, thresholds)
                logger.info("loop: critic satisfied → APPROVED plan=%s v%d", plan.id, plan.version)
                return LoopResult(
                    status="approved",
                    plan=plan,
                    findings=findings,
                    reason_code=CRITIC_SATISFIED,
                    approved_plan=approved,
                )

        if budget_exceeded(goal.constraints.budget, state):
            logger.info("loop: budget exceeded → escalate")
            return _escalate(goal, plan, findings, "budget_exceeded", revision)

        run_budget_hit = run_budget and run_budget.check()
        if run_budget_hit:
            return _escalate(goal, plan, findings, cast("ReasonCode", run_budget_hit), revision)

        if goal.replan_policy is ReplanPolicy.ABORT:
            logger.info("loop: replan_policy=abort → escalate (no revise)")
            return _escalate(goal, plan, findings, "replan_aborted", revision)

        if regression_detected(prior_findings, findings):
            logger.info("loop: regression detected → escalate (thrashing)")
            return _escalate(goal, plan, findings, "regression_thrashing", revision)

        if stalled(prior_plan, prior_findings, plan, findings):
            logger.info("loop: convergence detected → escalate (stalled)")
            return _escalate(goal, plan, findings, "converged_stalled", revision)

        if detect_oscillation(sig_history, config.oscillation_window):
            if config.converge_policy == "auto_converge":
                logger.info("loop: oscillation detected → auto-converge (partial approval)")
                # Build a merged plan with only non-oscillating tasks
                merged = _build_auto_converged_plan(plan, prior_plan)
                if merged is not None:
                    merged_findings = [
                        *findings,
                        Finding(
                            id=f"auto_converge:{plan.id}:{plan.version}",
                            version=plan.version,
                            severity=Severity.INFO,
                            reason_code="auto_converge_partial_approval",
                            message=(
                                "auto-converged non-oscillating tasks; oscillating subset escalated"
                            ),
                        ),
                    ]
                    threshold_ok, thresholds = evaluate_contract(merged_findings, contract)
                    if threshold_ok:
                        approved = approval.approve(merged, thresholds)
                        return LoopResult(
                            status="approved",
                            plan=merged,
                            findings=merged_findings,
                            reason_code="approved",
                            approved_plan=approved,
                        )
            logger.info("loop: oscillation detected → escalate")
            return _escalate(goal, plan, findings, "plan_oscillation_detected", revision)

        hist_history.append(compute_family_histogram(findings))
        # Cycling defers to structural oscillation (#152): when a trace
        # exhibits BOTH patterns, shape-based diagnosis (which tasks cycle)
        # is the richer escalation and wins. Deferral applies only while
        # oscillation is actually reachable; under stock revision_cap=3 the
        # oscillation window (4) can never fill (#232), so waiting on it
        # leaves cycling dead too — there the earliest reachable revision is
        # the detector's own minimum, histogram_lag + 1.
        cycle_start = (
            config.oscillation_window
            if config.revision_cap >= config.oscillation_window
            else config.histogram_lag + 1
        )
        if len(hist_history) >= cycle_start and detect_histogram_cycling(
            hist_history, config.histogram_lag
        ):
            logger.info("loop: family histogram cycling detected → escalate (reshuffling)")
            return _escalate(goal, plan, findings, "family_histogram_cycling", revision)

        prior_plan, prior_findings = plan, findings
        if revision < effective_cap:
            logger.info("loop: revising plan (revision %d → %d)", revision, revision + 1)
            next_id = f"plan-{goal.id}-r{revision + 1}"
            plan = _revise_or_raise(planner, plan, findings, next_id=next_id)
            if precondition_ledger is not None:
                precondition_ledger.process_plan(plan)
                for diag in precondition_ledger.diagnostics():
                    logger.info("ledger (post-revise): %s", diag.get("message", ""))

    logger.info("loop: revision cap reached → escalate")
    return _escalate(goal, plan, prior_findings or [], "revision_cap_reached", effective_cap)


def _has_blocker(findings: list[Finding]) -> bool:
    """True when any finding is a blocker (fail-closed threshold)."""
    return any(f.severity is Severity.BLOCKER for f in findings)


def _safe_audit(critic: CriticRole, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
    """Audit via critic, but never let a critic failure kill the loop.

    Args:
        critic: The critic role.
        plan: The plan being audited.
        findings: Gate findings flow through to the critic's view.

    Returns:
        The critic's findings. The deterministic gates have already
        collected gate findings separately, so a critic error maps to an
        empty list here rather than breaking the loop (the gates remain the
        free/immune layer). The error is logged so the failure is visible.
    """
    try:
        return critic.audit(plan, findings)
    except Exception as exc:
        logger.warning("critic role failed (full audit): %s — continuing with gates only", exc)
        return []


def _safe_audit_diff(
    critic: CriticRole,
    plan: PlanVersion,
    findings: list[Finding],
    prior_plan: PlanVersion | None,
) -> list[Finding]:
    """Diff-aware audit on revision N>1 when the critic supports it (F-78).

    Args:
        critic: The critic role.
        plan: The current plan revision.
        findings: Gate findings flowing through to the critic's view.
        prior_plan: The previous revision, or None on the root revision.

    Returns:
        The critic's findings. A full audit is used on the root revision or
        when the critic has no diff-aware entry point.
    """
    diff_method = getattr(critic, "audit_diff", None)
    if diff_method is None:
        return _safe_audit(critic, plan, findings)
    audit_diff = cast(
        "Callable[[PlanVersion, list[Finding], Sequence[str]], list[Finding]]",
        diff_method,
    )
    try:
        scope = scope_between(prior_plan, plan)
        return audit_diff(plan, findings, scope)
    except Exception as exc:
        logger.warning("critic role failed (diff audit): %s — continuing with gates only", exc)
        return []


def _revise_or_raise(
    planner: PlannerRole,
    plan: PlanVersion,
    findings: list[Finding],
    next_id: str,
) -> PlanVersion:
    """Revise the plan; raise fail-closed PlanningError on role failure.

    Args:
        planner: The planner role.
        plan: The current revision.
        findings: Findings the revision must address.
        next_id: Id to stamp on the new revision.

    Returns:
        The new revision as a typed PlanVersion.

    Raises:
        PlanningError: When the planner fails or emits something untyped.
    """
    try:
        revised = planner.revise(plan, findings)
    except Exception as exc:
        raise PlanningError(f"planner role failed to revise: {exc}") from exc
    if not isinstance(revised, PlanVersion):
        raise PlanningError(f"planner revised to non-PlanVersion: {type(revised).__name__}")
    return _plan_revision(revised, next_id, parent=plan)


__all__ = ["CriticMode", "LoopConfig", "LoopResult", "meets_threshold", "run_loop"]
