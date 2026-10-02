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
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


def build_run_detail(
    story: dict[str, Any], artifacts: list[dict[str, Any]] | None = None
) -> RunDetailView:
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
        artifacts=artifacts or [],
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
    reason: str | None = None
    severity: str | None = None
    status: str = "active"
    quarantine_id: str | None = None


class CertDashboardView(BaseModel):
    """The certification dashboard: status, trends, and quarantine history."""

    model_config = ConfigDict(extra="forbid")

    status_counts: dict[str, int] = Field(default_factory=dict)
    trends: list[CertTrendPoint] = Field(default_factory=list)
    last_certified: list[LastCertified] = Field(default_factory=list)
    quarantine_history: list[QuarantineEntry] = Field(default_factory=list)


def build_cert_dashboard(
    records: list[dict[str, Any]],
    quarantines: list[dict[str, Any]] | None = None,
) -> CertDashboardView:
    """Aggregate certification records into the dashboard view.

    ``quarantines`` are optional persisted drift-quarantine records (M34-05);
    when supplied their reason/severity/status enrich the quarantine history.
    """
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

    if quarantines:
        quarantine = [
            QuarantineEntry(
                workload=str(item.get("workload", "")),
                timestamp=str(item.get("timestamp", "")),
                record_id=str(item.get("quarantine_id", "")),
                reason=item.get("reason"),
                severity=item.get("severity"),
                status=str(item.get("status", "active")),
                quarantine_id=str(item.get("quarantine_id", "")),
            )
            for item in quarantines
        ]

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


# --------------------------------------------------------------------------- #
# M52 — queue, health, ROI, search, diff, onboarding
# --------------------------------------------------------------------------- #
class QueueItemView(BaseModel):
    """One queued task in the queue visualizer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    workload: str
    qos: str
    priority: int = 0
    reason: str = "awaiting capacity"


class QueueView(BaseModel):
    """Queue depth, priorities, and running load."""

    model_config = ConfigDict(extra="forbid")

    depth: int = 0
    by_qos: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    items: list[QueueItemView] = Field(default_factory=list)
    running_by_workload: dict[str, int] = Field(default_factory=dict)


def build_queue(snapshot: dict[str, Any]) -> QueueView:
    """Build the queue visualizer view from a queue snapshot."""
    return QueueView(
        depth=int(snapshot.get("depth", 0)),
        by_qos={str(k): int(v) for k, v in snapshot.get("by_qos", {}).items()},
        by_priority={str(k): int(v) for k, v in snapshot.get("by_priority", {}).items()},
        items=[
            QueueItemView(
                task_id=str(item.get("task_id", "")),
                workload=str(item.get("workload", "")),
                qos=str(item.get("qos", "")),
                priority=int(item.get("priority", 0)),
                reason=str(item.get("reason", "awaiting capacity")),
            )
            for item in snapshot.get("waiting", [])
        ],
        running_by_workload={
            str(k): int(v) for k, v in snapshot.get("running_by_workload", {}).items()
        },
    )


class HealthWorkloadView(BaseModel):
    """One workload's health row."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    status: str = "unknown"
    success_rate: float = 0.0
    error_budget_remaining: float = 0.0


class HealthView(BaseModel):
    """The health dashboard: per-workload status and SLO budget."""

    model_config = ConfigDict(extra="forbid")

    healthy: int = 0
    degraded: int = 0
    workloads: list[HealthWorkloadView] = Field(default_factory=list)


def build_health(workloads: list[dict[str, Any]]) -> HealthView:
    """Build the health dashboard from workload health payloads."""
    rows = [
        HealthWorkloadView(
            workload=str(item.get("workload") or item.get("name", "")),
            status=str(item.get("status", "unknown")),
            success_rate=float(item.get("success_rate", 0.0)),
            error_budget_remaining=float(item.get("error_budget_remaining", 0.0)),
        )
        for item in workloads
    ]
    healthy = sum(1 for row in rows if row.status in ("healthy", "ok"))
    return HealthView(
        healthy=healthy, degraded=len(rows) - healthy, workloads=rows
    )


def build_health_from_api(workloads: list[dict[str, Any]]) -> HealthView:
    """Map control-plane ``WorkloadHealth`` rows into the health view.

    ``failure_rate`` (0..1) becomes ``success_rate = 1 - failure_rate``; the
    error-budget remaining is taken from the ``availability`` objective when
    present, otherwise it defaults to ``0.0``.
    """
    mapped: list[dict[str, Any]] = []
    for item in workloads:
        objectives = item.get("objectives") or []
        availability = next(
            (obj for obj in objectives if obj.get("objective") == "availability"),
            None,
        )
        mapped.append(
            {
                "workload": str(item.get("workload", "")),
                "status": str(item.get("status", "unknown")),
                "success_rate": 1.0 - float(item.get("failure_rate", 0.0)),
                "error_budget_remaining": (
                    float(availability.get("remaining", 0.0))
                    if availability is not None
                    else 0.0
                ),
            }
        )
    return build_health(mapped)


class RoiRowView(BaseModel):
    """One ROI row with its evidence-backed flag."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    spend_usd: float = 0.0
    value_usd: float = 0.0
    roi: float = 0.0
    expensive_low_value: bool = False
    evidence: list[str] = Field(default_factory=list)


class RoiView(BaseModel):
    """The ROI dashboard: spend vs outcome and low-value flags."""

    model_config = ConfigDict(extra="forbid")

    fleet_roi: float = 0.0
    total_spend_usd: float = 0.0
    rows: list[RoiRowView] = Field(default_factory=list)

    @property
    def flagged(self) -> list[RoiRowView]:
        """Return the flagged expensive-but-low-value rows."""
        return [row for row in self.rows if row.expensive_low_value]


def build_roi(payload: dict[str, Any]) -> RoiView:
    """Build the ROI dashboard from a fleet ROI report."""
    return RoiView(
        fleet_roi=float(payload.get("fleet_roi", 0.0)),
        total_spend_usd=float(payload.get("total_spend_usd", 0.0)),
        rows=[
            RoiRowView(
                workload=str(row.get("workload_id", "")),
                spend_usd=float(row.get("spend_usd", 0.0)),
                value_usd=float(row.get("value_usd", 0.0)),
                roi=float(row.get("roi", 0.0)),
                expensive_low_value=bool(row.get("expensive_low_value", False)),
                evidence=[str(e) for e in row.get("evidence", [])],
            )
            for row in payload.get("rows", [])
        ],
    )


class SearchHitView(BaseModel):
    """One global-search hit."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    identifier: str
    label: str = ""


class SearchView(BaseModel):
    """Global search results across runs, approvals, and workloads."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    hits: list[SearchHitView] = Field(default_factory=list)


def build_search(query: str, hits: list[dict[str, Any]]) -> SearchView:
    """Build the global-search view."""
    return SearchView(
        query=query,
        hits=[
            SearchHitView(
                kind=str(hit.get("kind", "")),
                identifier=str(hit.get("identifier", "")),
                label=str(hit.get("label", "")),
            )
            for hit in hits
        ],
    )


class DiffEntryView(BaseModel):
    """One changed field in a run/manifest diff."""

    model_config = ConfigDict(extra="forbid")

    field: str
    before: str = ""
    after: str = ""


class DiffView(BaseModel):
    """A regression or run-to-run diff."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    entries: list[DiffEntryView] = Field(default_factory=list)


def build_diff(payload: dict[str, Any]) -> DiffView:
    """Build a diff view from a diff payload."""
    return DiffView(
        title=str(payload.get("title", "Diff")),
        entries=[
            DiffEntryView(
                field=str(entry.get("field", "")),
                before=str(entry.get("before", "")),
                after=str(entry.get("after", "")),
            )
            for entry in payload.get("entries", [])
        ],
    )


def diff_from_version_diff(payload: dict[str, Any]) -> DiffView:
    """Map a registry ``VersionDiff`` into a diff view.

    Each changed field is shown as a marker entry since the API reports field
    names only, not their prior/next values.
    """
    return DiffView(
        title=(
            f"{payload.get('workload', '')} "
            f"v{payload.get('from_version', '')}\u2192v{payload.get('to_version', '')}"
        ),
        entries=[
            DiffEntryView(field=str(field), before="\u2014", after="changed")
            for field in payload.get("changed_fields", [])
        ],
    )


def diff_from_regression(payload: dict[str, Any]) -> DiffView:
    """Map a certification ``RegressionDiff`` into a diff view."""
    deltas = list(payload.get("regressed", [])) + list(payload.get("improved", []))
    return DiffView(
        title=f"{payload.get('workload_id', '')} regression",
        entries=[
            DiffEntryView(
                field=str(delta.get("task_id", "")),
                before=str(delta.get("before", "")),
                after=str(delta.get("after", "")),
            )
            for delta in deltas
        ],
    )


_RUN_DIFF_FIELDS = ("state", "cost_usd", "model_identity")


def diff_from_runs(before: dict[str, Any], after: dict[str, Any]) -> DiffView:
    """Compare two run stories' top-level state, cost, and model identity."""
    return DiffView(
        title=f"run {before.get('run_id', '')}\u2192{after.get('run_id', '')}",
        entries=[
            DiffEntryView(
                field=field,
                before=str(before.get(field, "")),
                after=str(after.get(field, "")),
            )
            for field in _RUN_DIFF_FIELDS
            if before.get(field) != after.get(field)
        ],
    )


class ReplayFrameView(BaseModel):
    """One reconstructed run frame (M60)."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = 0
    event_type: str = ""
    state: str = ""
    detail: str = ""
    usage: str = ""


class ReplayView(BaseModel):
    """A frame-by-frame replay of a run (M60)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = ""
    digest: str = ""
    side_effects: bool = False
    frames: list[ReplayFrameView] = Field(default_factory=list)


def build_replay(payload: dict[str, Any]) -> ReplayView:
    """Map a replay frame set into a display-ready view."""
    frames = []
    for frame in payload.get("frames", []):
        before = frame.get("from_state")
        after = frame.get("to_state")
        state = f"{before} \u2192 {after}" if before or after else ""
        usage = frame.get("usage") or {}
        frames.append(
            ReplayFrameView(
                sequence=int(frame.get("sequence", 0)),
                event_type=str(frame.get("event_type", "")),
                state=state,
                detail=str(frame.get("detail") or ""),
                usage=str(usage.get("model_identity") or "") if usage else "",
            )
        )
    return ReplayView(
        run_id=str(payload.get("run_id", "")),
        digest=str(payload.get("digest", "")),
        side_effects=bool(payload.get("side_effects", False)),
        frames=frames,
    )


def diff_from_replay(payload: dict[str, Any]) -> DiffView:
    """Map a run-to-run ``RunDiff`` into a diff view (M60)."""
    source = payload.get("source_run_id", "")
    target = payload.get("target_run_id", "")
    suffix = " (identical)" if payload.get("identical") else ""
    return DiffView(
        title=f"run {source}\u2192{target}{suffix}",
        entries=[
            DiffEntryView(
                field=str(delta.get("field", "")),
                before=str(delta.get("before", "")),
                after=str(delta.get("after", "")),
            )
            for delta in payload.get("field_deltas", [])
        ],
    )


class OnboardingStep(BaseModel):
    """One onboarding-wizard step and its completion state."""

    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    done: bool = False
    href: str = ""


class OnboardingView(BaseModel):
    """The onboarding wizard: register → certify → first trigger."""

    model_config = ConfigDict(extra="forbid")

    steps: list[OnboardingStep] = Field(default_factory=list)

    @property
    def completed(self) -> int:
        """Return the number of completed steps."""
        return sum(1 for step in self.steps if step.done)


_ONBOARDING_STEPS = (
    ("connect", "Connect a model", "/settings/model"),
    ("register", "Register a workload", "/workloads"),
    ("certify", "Certify the workload", "/certifications"),
    ("trigger", "Fire the first trigger", "/triggers"),
)


def build_onboarding(done: set[str] | None = None) -> OnboardingView:
    """Build the onboarding wizard with the completed step keys."""
    completed = done or set()
    return OnboardingView(
        steps=[
            OnboardingStep(key=key, title=title, done=key in completed, href=href)
            for key, title, href in _ONBOARDING_STEPS
        ]
    )
