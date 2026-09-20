"""Approval expiry / stale-plan handling (F-18, PRD §2.7e).

An approved plan carries an ``approval_ttl`` on the goal. If the approval is
older than the TTL, the plan is stale and must be replanned before use. The
TTL comes from :class:`~planner_critic.schema.goal.Goal.approval_ttl`; the
stale decision is consumed by the re-gate / replan path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..schema.goal import ReplanPolicy
from ..types import ApprovedPlan


def approval_expired(
    approved_at: datetime,
    approval_ttl: timedelta | None,
    now: datetime | None = None,
) -> bool:
    """True when an approval has outlived its TTL.

    Args:
        approved_at: When the plan was approved.
        approval_ttl: Time-to-live; None means the approval never expires.
        now: Reference clock (injectable for tests; defaults to UTC now).

    Returns:
        True when ``approval_ttl`` is set and ``now - approved_at`` exceeds it.
    """
    if approval_ttl is None:
        return False
    reference = now if now is not None else datetime.now(UTC)
    return (reference - approved_at) > approval_ttl


@dataclass(frozen=True)
class StalenessCheck:
    """The outcome of evaluating an approved plan against its TTL (F-18).

    Attributes:
        stale: True when the approval has outlived its TTL.
        replan_policy: The goal's replan policy (patch/restart/abort).
        reason: ``"ttl_expired"`` when stale; None when fresh (or no TTL).
    """

    stale: bool
    replan_policy: ReplanPolicy
    reason: str | None = None


def check_staleness(
    approved: ApprovedPlan,
    replan_policy: ReplanPolicy,
    approval_ttl: timedelta | None,
    now: datetime | None = None,
) -> StalenessCheck:
    """Evaluate whether an ``ApprovedPlan`` must be replanned (F-18).

    Args:
        approved: The approved plan under review.
        replan_policy: What to do when stale (patch/restart/abort).
        approval_ttl: The approval time-to-live; None means never expires.
        now: Reference clock (injectable for tests; defaults to UTC now).

    Returns:
        A :class:`StalenessCheck` — ``stale=True`` drives a forced replan.
    """
    if approval_ttl is None:
        return StalenessCheck(stale=False, replan_policy=replan_policy)
    expired = approval_expired(approved.approved_at, approval_ttl, now=now)
    return StalenessCheck(
        stale=expired,
        replan_policy=replan_policy,
        reason="ttl_expired" if expired else None,
    )
