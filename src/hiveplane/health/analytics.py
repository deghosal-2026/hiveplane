"""Approval analytics: latency by approver, bottleneck, and trends (M43-05)."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus


class ApprovalReport(BaseModel):
    """Read-only analytics derived from approval events."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    decided: int = Field(ge=0)
    approved: int = Field(ge=0)
    denied: int = Field(ge=0)
    mean_latency_seconds: float | None = None
    by_approver: dict[str, float] = Field(default_factory=dict)
    bottleneck: str | None = None


def approval_analytics(records: list[ApprovalRecord]) -> ApprovalReport:
    """Compute per-approver latency, the bottleneck, and approve/deny trends."""
    latencies: dict[str, list[float]] = defaultdict(list)
    all_latencies: list[float] = []
    approved = denied = 0
    for record in records:
        if record.status is ApprovalStatus.APPROVED:
            approved += 1
        elif record.status is ApprovalStatus.DENIED:
            denied += 1
        if record.decided_at is None or record.decided_by is None:
            continue
        seconds = (record.decided_at - record.requested_at).total_seconds()
        latencies[record.decided_by].append(seconds)
        all_latencies.append(seconds)
    by_approver = {
        approver: sum(values) / len(values) for approver, values in latencies.items()
    }
    bottleneck = (
        max(by_approver, key=lambda name: by_approver[name]) if by_approver else None
    )
    return ApprovalReport(
        total=len(records),
        pending=sum(1 for r in records if r.status is ApprovalStatus.PENDING),
        decided=approved + denied,
        approved=approved,
        denied=denied,
        mean_latency_seconds=(
            sum(all_latencies) / len(all_latencies) if all_latencies else None
        ),
        by_approver=by_approver,
        bottleneck=bottleneck,
    )
