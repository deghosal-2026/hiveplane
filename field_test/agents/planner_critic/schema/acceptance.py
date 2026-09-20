"""Frozen acceptance-criteria contract (#215).

The criteria and the approving authority are bound **before execution
starts**. The planner and critic may revise the graph, but they can never
change what "passing" means for the run already in flight:

* :func:`bind_acceptance` snapshots the goal's posture into an immutable
  :class:`AcceptanceContract` with a content hash.
* :func:`evaluate_contract` is a pure function of ``(findings, contract)`` —
  approval reads the bound posture, never ambient config.
* :func:`revise_contract` never mutates: it returns a new contract with an
  incremented version and the prior hash preserved for audit. Changing the
  rules mid-run therefore forces a new lineage instead of easing the current
  one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from ..approval import ThresholdOutcome, resolve_threshold
from ..types import Finding
from .goal import Goal, RiskTolerance


class AcceptanceCriterion(BaseModel):
    """One acceptance rule frozen into the contract.

    ``kind="risk_tolerance"`` carries the approval posture the loop must
    apply; additional kinds (custom policy refs) may be added without
    changing the contract's identity scheme because the content hash covers
    the whole serialized model.
    """

    kind: str = Field(description='Criterion kind, e.g. "risk_tolerance"')
    value: str = Field(description="Machine-readable criterion value")


class AcceptanceContract(BaseModel):
    """Immutable acceptance criteria + approving authority, bound pre-run."""

    model_config = ConfigDict(frozen=True)

    goal_id: str
    criteria: tuple[AcceptanceCriterion, ...]
    approving_authority: str
    contract_version: int = Field(ge=1)
    prior_hashes: tuple[str, ...] = ()
    bound_at: datetime
    content_hash: str

    def risk_tolerance(self) -> RiskTolerance:
        """The bound approval posture (the contract's primary criterion)."""
        for criterion in self.criteria:
            if criterion.kind == "risk_tolerance":
                return RiskTolerance(criterion.value)
        return RiskTolerance.BALANCED


def _content_hash(
    goal_id: str, criteria: tuple[AcceptanceCriterion, ...], approving_authority: str
) -> str:
    """Stable SHA-256 over the canonical serialized inputs.

    Criteria enter the hash as an order-normalized ``(kind, value)`` sequence
    (#241): the same rule set binds to one identity regardless of insertion
    order, while genuinely different rule sets still diverge. Normalization
    is a no-op for single-criterion contracts, preserving every hash already
    bound by :func:`bind_acceptance`.
    """
    payload = repr(((goal_id, sorted((c.kind, c.value) for c in criteria)), approving_authority))
    return sha256(payload.encode("utf-8")).hexdigest()


def bind_acceptance(goal: Goal, *, approving_authority: str = "engine") -> AcceptanceContract:
    """Bind a contract from the goal's current posture before the loop runs.

    Args:
        goal: The typed planning request.
        approving_authority: Principal allowed to approve/deny escalations.

    Returns:
        A frozen v1 contract whose hash pins goal id + criteria + authority.
    """
    criteria = (AcceptanceCriterion(kind="risk_tolerance", value=goal.risk_tolerance.value),)
    return AcceptanceContract(
        goal_id=goal.id,
        criteria=criteria,
        approving_authority=approving_authority,
        contract_version=1,
        prior_hashes=(),
        bound_at=datetime.now(UTC),
        content_hash=_content_hash(goal.id, criteria, approving_authority),
    )


def revise_contract(
    contract: AcceptanceContract,
    *,
    approving_authority: str | None = None,
) -> AcceptanceContract:
    """Derive the next contract version; the current run is never eased.

    Args:
        contract: The contract currently bound to the run.
        approving_authority: New authority, or ``None`` to carry forward.

    Returns:
        A new frozen contract: version + 1, fresh hash, prior hash appended.
    """
    new_authority = approving_authority or contract.approving_authority
    new_hash = _content_hash(contract.goal_id, contract.criteria, new_authority)
    return AcceptanceContract(
        goal_id=contract.goal_id,
        criteria=contract.criteria,
        approving_authority=new_authority,
        contract_version=contract.contract_version + 1,
        prior_hashes=(*contract.prior_hashes, contract.content_hash),
        bound_at=datetime.now(UTC),
        content_hash=new_hash,
    )


def evaluate_contract(
    findings: list[Finding], contract: AcceptanceContract
) -> tuple[bool, ThresholdOutcome]:
    """Deterministic approval decision against the bound contract only.

    Args:
        findings: All findings for the plan revision under judgment.
        contract: The bound acceptance contract.

    Returns:
        ``(satisfied, outcome)`` exactly as :func:`resolve_threshold`, but
        keyed off the contract's frozen posture rather than ambient config.
    """
    return resolve_threshold(findings, contract.risk_tolerance())


__all__ = [
    "AcceptanceContract",
    "AcceptanceCriterion",
    "bind_acceptance",
    "evaluate_contract",
    "revise_contract",
]
