"""Escalation manager (F-30, F-34): the precise human-in-the-loop gate.

When the loop cannot converge, it produces a :class:`Escalation` — one
*precise, resolvable* question (DD-10). The escalation manager is the
concrete gate between that record and a human decision:

* :meth:`create` validates the single-question precision contract and
  persists the escalation in the plan store (side-channel safe).
* :meth:`list_escalations` surfaces open/pending escalations for a reviewer.
* :meth:`resolve` records the human decision and timestamp back into the
  plan's history (the escalation is part of the persistent plan record, so
  the full arc is replay-able).
* :meth:`patch_and_recritique` implements *direct plan patching before
  approval*: a reviewer-supplied PlanVersion is stored as the next revision,
  re-run through the deterministic gates + critic, and its findings
  persisted. A patch that still trips a deterministic blocker is refused
  fail-closed (the LLM critic can never override a gate blocker).

The manager never holds decisions in memory: the store is the single source
of truth, so the escalation lifecycle survives restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from .gates import run_deterministic_gates
from .roles import CriticRole
from .schema.acceptance import AcceptanceContract
from .schema.plan import PlanVersion
from .store.base import PlanStore
from .types import Escalation, Finding, Severity


class EscalationManager:
    """Create, list, and resolve precise plan escalations against a store.

    Args:
        store: The plan store that persists escalations, plans, and findings.
        approving_authority: Optional bound principal (from an
            :class:`~planner_critic.schema.acceptance.AcceptanceContract`).
            When set, ``resolve`` requires a matching ``principal`` — the
            authority is pinned at bind time and cannot drift mid-run.
    """

    def __init__(self, store: PlanStore, approving_authority: str | None = None) -> None:
        """Bind the manager to its backing store and optional bound authority."""
        self._store = store
        self._approving_authority = approving_authority

    def create(self, escalation: Escalation) -> Escalation:
        """Validate precision contract and persist an open escalation.

        Args:
            escalation: The escalation record to persist.

        Returns:
            The persisted escalation (status ``open``).

        Raises:
            ValueError: When the escalation is not precise — the target plan
                or revision is unknown to the store, the question is blank,
                or the plan already has an open escalation.
        """
        current = self._store.get_plan(escalation.plan_id, escalation.version)
        if current is None:
            raise ValueError(
                f"cannot escalate unknown plan {escalation.plan_id!r} revision {escalation.version}"
            )
        if not escalation.question.strip():
            raise ValueError("escalation question must not be blank (DD-10)")
        existing = self._store.get_escalation(escalation.plan_id)
        if existing is not None and existing.status == "open":
            raise ValueError(f"plan {escalation.plan_id!r} already has an open escalation")
        self._store.put_escalation(escalation)
        return escalation

    def list_escalations(
        self, status: Literal["open", "approved", "denied"] | None = None
    ) -> list[Escalation]:
        """Return all escalations, optionally filtered by status.

        Args:
            status: Restrict the listing to a single status; None returns all.

        Returns:
            Escalations in plan-list order.
        """
        result: list[Escalation] = []
        for plan in self._store.list_plans():
            escalation = self._store.get_escalation(plan.id)
            if escalation is not None and (status is None or escalation.status == status):
                result.append(escalation)
        return result

    def resolve(
        self,
        escalation_id: str,
        decision: Literal["approved", "denied"],
        note: str = "",
        principal: str | None = None,
    ) -> Escalation:
        """Record a human decision against an open escalation.

        Args:
            escalation_id: Which escalation to resolve.
            decision: The human's decision (``approved`` or ``denied``).
            note: Optional resolution text recorded in the plan history.
            principal: Who is resolving. Required to match the bound
                approving authority when one was configured.

        Returns:
            The resolved escalation (status + timestamp updated).

        Raises:
            ValueError: When the escalation is unknown or already resolved.
            PermissionError: When a bound approving authority exists and
                ``principal`` does not match it.
        """
        if self._approving_authority is not None and principal != self._approving_authority:
            raise PermissionError(
                f"principal {principal!r} is not the bound approving authority "
                f"{self._approving_authority!r}"
            )
        escalation = self._get_by_id(escalation_id)
        if escalation.status != "open":
            raise ValueError(f"escalation {escalation_id!r} is already resolved")
        resolved = escalation.model_copy(
            update={
                "status": decision,
                "resolution": note,
                "resolved_at": datetime.now(UTC),
                "resolved_by": principal,
            }
        )
        self._store.put_escalation(resolved)
        return resolved

    def patch_and_recritique(
        self,
        plan_id: str,
        patch: PlanVersion,
        critic: CriticRole,
    ) -> list[Finding]:
        """Store a reviewer patch as the next revision and re-critique it.

        Args:
            plan_id: The plan being patched.
            patch: The reviewer-supplied revision (version should be the
                previous version + 1, parented at the previous revision).
            critic: The critic role used for re-auditing.

        Returns:
            The findings produced against the patched revision.

        Raises:
            ValueError: When the patched plan still has a deterministic gate
                blocker (fail-closed; the LLM cannot override a gate blocker).
        """
        current = self._store.get_plan(plan_id)
        if current is None:
            raise ValueError(f"cannot patch unknown plan {plan_id!r}")
        if patch.version != current.version + 1 or patch.parent_version != current.id:
            raise ValueError(
                f"patch must be revision {current.version + 1} of plan "
                f"{current.id!r}, parented at {current.id!r}"
            )

        gate_findings = run_deterministic_gates(patch)
        critic_findings = critic.audit(patch, gate_findings)
        seen: dict[str, Finding] = {f.id: f for f in gate_findings}
        for finding in critic_findings:
            seen.setdefault(finding.id, finding)
        findings = list(seen.values())

        blockers = [f for f in findings if f.severity is Severity.BLOCKER and not f.is_llm_finding]
        if blockers:
            raise ValueError(
                f"patched plan is not clean: {len(blockers)} deterministic blocker(s) remain — "
                "a deterministic-gate blocker can never be approved (F-73)"
            )

        self._store.put_plan_version(patch)
        self._store.put_findings(plan_id, patch.version, findings)
        return findings

    def _get_by_id(self, escalation_id: str) -> Escalation:
        """Locate an escalation by its unique id.

        Args:
            escalation_id: The escalation id to find.

        Returns:
            The matching escalation.

        Raises:
            ValueError: When no escalation has that id.
        """
        for escalation in self.list_escalations():
            if escalation.id == escalation_id:
                return escalation
        raise ValueError(f"unknown escalation {escalation_id!r}")


__all__ = ["EscalationManager", "build_escalation_manager"]


def build_escalation_manager(
    store: PlanStore,
    goal_id: str | None = None,
    escalation_id: str | None = None,
) -> EscalationManager:
    """Build an EscalationManager with authority pre-bound from the store.

    Looks up the :class:`~planner_critic.schema.acceptance.AcceptanceContract`
    from the store and extracts its ``approving_authority``. When no contract
    exists (legacy runs), the manager is constructed without bound authority
    and ``resolve`` accepts any principal.

    Args:
        store: The plan store containing the contract.
        goal_id: Look up the contract by goal id directly.
        escalation_id: Alternative — look up the escalation's plan to derive
            the goal id. Only one of ``goal_id`` or ``escalation_id`` should
            be provided.

    Returns:
        An ``EscalationManager`` with ``approving_authority`` pre-bound.
    """
    if goal_id is None and escalation_id is not None:
        try:
            esc = store.get_escalation(escalation_id)
            if esc is not None:
                plan = store.get_plan(esc.plan_id)
                if plan is not None:
                    goal_id = plan.goal_id
        except Exception:
            pass

    authority: str | None = None
    if goal_id is not None:
        try:
            contract = store.get_acceptance_contract(goal_id)
            if contract is not None:
                authority = contract.approving_authority
        except Exception:
            pass

    return EscalationManager(store, approving_authority=authority)
