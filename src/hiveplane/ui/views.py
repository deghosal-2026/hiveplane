"""Pure view models for the operator UI screens (M22, #85).

These functions turn control-plane API payloads into display-ready data. They
perform no I/O and hold no framework state, so they are cheap to test and easy
to reason about.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_CERT_STATUSES = ("uncertified", "provisional", "certified", "quarantined")
_RUN_STATES = ("queued", "running", "paused", "completed", "failed", "cancelled")
_FAILURE_STATES = ("failed", "cancelled")


# --------------------------------------------------------------------------- #
# Fleet
# --------------------------------------------------------------------------- #
class FleetWorkload(BaseModel):
    """One workload's row in the fleet list."""

    model_config = ConfigDict(extra="forbid")

    name: str
    owner: str
    team: str | None = None
    certification_status: str
    state_counts: dict[str, int]
    recent_failures: int
    last_run_at: str | None = None
    budget_burn_usd: float = 0.0


class FleetView(BaseModel):
    """The fleet list: workload rows plus fleet-level rollups."""

    model_config = ConfigDict(extra="forbid")

    workloads: list[FleetWorkload] = Field(default_factory=list)
    total_workloads: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    total_spend_usd: float = 0.0


def build_fleet(
    workloads: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    spend: dict[str, Any],
) -> FleetView:
    """Aggregate the catalog, runs, and spend into the fleet view."""
    runs_by_workload: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        runs_by_workload.setdefault(str(run.get("workload_id", "")), []).append(run)

    spend_by_workload = {
        str(row.get("workload", "")): float(row.get("total_usd", 0.0))
        for row in spend.get("by_workload", [])
    }

    status_counts = dict.fromkeys(_CERT_STATUSES, 0)
    rows: list[FleetWorkload] = []
    for workload in workloads:
        name = str(workload.get("name", ""))
        status = str(workload.get("certification_status", "uncertified"))
        if status in status_counts:
            status_counts[status] += 1

        state_counts = dict.fromkeys(_RUN_STATES, 0)
        failures = 0
        for run in runs_by_workload.get(name, []):
            state = str(run.get("state", ""))
            if state in state_counts:
                state_counts[state] += 1
            if state in _FAILURE_STATES:
                failures += 1

        rows.append(
            FleetWorkload(
                name=name,
                owner=str(workload.get("owner", "")),
                team=workload.get("team"),
                certification_status=status,
                state_counts=state_counts,
                recent_failures=failures,
                last_run_at=workload.get("last_run_at"),
                budget_burn_usd=spend_by_workload.get(name, 0.0),
            )
        )

    return FleetView(
        workloads=rows,
        total_workloads=len(rows),
        status_counts=status_counts,
        total_spend_usd=sum(spend_by_workload.values()),
    )


# --------------------------------------------------------------------------- #
# Run detail
# --------------------------------------------------------------------------- #
class StoryEntryView(BaseModel):
    """One moment in a run's execution story."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str
    kind: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)


class RunDetailView(BaseModel):
    """A run's execution story, rendered for the detail screen."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = ""
    workload: str = ""
    team: str | None = None
    state: str = ""
    context: str | None = None
    model_identity: str | None = None
    cost_usd: float = 0.0
    sandbox: bool = False
    sandbox_id: str | None = None
    certification_status: str = ""
    attestation_id: str | None = None
    trace_id: str | None = None
    entries: list[StoryEntryView] = Field(default_factory=list)


def build_run_detail(story: dict[str, Any]) -> RunDetailView:
    """Build the run detail view from a run story payload."""
    entries = [
        StoryEntryView(
            timestamp=str(entry.get("timestamp", "")),
            kind=str(entry.get("kind", "")),
            summary=str(entry.get("summary", "")),
            detail=entry.get("detail") or {},
        )
        for entry in story.get("entries", [])
    ]
    return RunDetailView(
        run_id=str(story.get("run_id", "")),
        workload=str(story.get("workload", "")),
        team=story.get("team"),
        state=str(story.get("state", "")),
        context=story.get("context"),
        model_identity=story.get("model_identity"),
        cost_usd=float(story.get("cost_usd") or 0.0),
        sandbox=bool(story.get("sandbox", False)),
        sandbox_id=story.get("sandbox_id"),
        certification_status=str(story.get("certification_status", "")),
        attestation_id=story.get("attestation_id"),
        trace_id=story.get("trace_id"),
        entries=entries,
    )


# --------------------------------------------------------------------------- #
# Certification dashboard
# --------------------------------------------------------------------------- #
class CertTrendPoint(BaseModel):
    """One certification's pass rate on a workload's trend line."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    timestamp: str
    pass_rate: float | None = None
    status: str
    attestation_id: str | None = None


class LastCertified(BaseModel):
    """The most recent certification for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    timestamp: str
    status: str


class QuarantineEntry(BaseModel):
    """A quarantine event in the fleet's history."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    timestamp: str
    record_id: str


class CertDashboardView(BaseModel):
    """The certification dashboard: status, trends, and quarantine history."""

    model_config = ConfigDict(extra="forbid")

    status_counts: dict[str, int] = Field(default_factory=dict)
    trends: list[CertTrendPoint] = Field(default_factory=list)
    last_certified: list[LastCertified] = Field(default_factory=list)
    quarantine_history: list[QuarantineEntry] = Field(default_factory=list)


def build_cert_dashboard(records: list[dict[str, Any]]) -> CertDashboardView:
    """Aggregate certification records into the dashboard view."""
    status_counts = dict.fromkeys(_CERT_STATUSES, 0)
    trends: list[CertTrendPoint] = []
    quarantine: list[QuarantineEntry] = []
    latest: dict[str, tuple[str, str]] = {}

    for record in records:
        certification = record.get("certification", {})
        workload = str(certification.get("workload_id", ""))
        status = str(certification.get("status", ""))
        timestamp = str(certification.get("timestamp", ""))
        if status in status_counts:
            status_counts[status] += 1

        summary = certification.get("eval_summary") or {}
        trends.append(
            CertTrendPoint(
                workload=workload,
                timestamp=timestamp,
                pass_rate=summary.get("pass_rate"),
                status=status,
                attestation_id=certification.get("attestation_id"),
            )
        )
        if workload not in latest or timestamp > latest[workload][0]:
            latest[workload] = (timestamp, status)
        if status == "quarantined":
            quarantine.append(
                QuarantineEntry(
                    workload=workload,
                    timestamp=timestamp,
                    record_id=str(record.get("record_id", "")),
                )
            )

    trends.sort(key=lambda point: (point.workload, point.timestamp))
    quarantine.sort(key=lambda entry: (entry.workload, entry.timestamp))
    last_certified = [
        LastCertified(workload=workload, timestamp=timestamp, status=status)
        for workload, (timestamp, status) in sorted(latest.items())
    ]
    return CertDashboardView(
        status_counts=status_counts,
        trends=trends,
        last_certified=last_certified,
        quarantine_history=quarantine,
    )


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #
class SpendWorkloadRow(BaseModel):
    """Spend rolled up for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    team: str | None = None
    total_usd: float = 0.0
    run_count: int = 0


class SpendTeamRow(BaseModel):
    """Spend rolled up for a team."""

    model_config = ConfigDict(extra="forbid")

    team: str
    total_usd: float = 0.0
    run_count: int = 0


class SpendView(BaseModel):
    """The spend view: totals by workload and by team."""

    model_config = ConfigDict(extra="forbid")

    by_workload: list[SpendWorkloadRow] = Field(default_factory=list)
    by_team: list[SpendTeamRow] = Field(default_factory=list)
    total_usd: float = 0.0


def build_spend(spend: dict[str, Any]) -> SpendView:
    """Build the spend view from a spend summary payload."""
    by_workload = [
        SpendWorkloadRow(
            workload=str(row.get("workload", "")),
            team=row.get("team"),
            total_usd=float(row.get("total_usd", 0.0)),
            run_count=int(row.get("run_count", 0)),
        )
        for row in spend.get("by_workload", [])
    ]
    by_team = [
        SpendTeamRow(
            team=str(row.get("team", "")),
            total_usd=float(row.get("total_usd", 0.0)),
            run_count=int(row.get("run_count", 0)),
        )
        for row in spend.get("by_team", [])
    ]
    return SpendView(
        by_workload=by_workload,
        by_team=by_team,
        total_usd=sum(row.total_usd for row in by_workload),
    )
