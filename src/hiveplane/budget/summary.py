"""Spend aggregation over cost attributions (M22, #82)."""

from __future__ import annotations

from collections.abc import Sequence

from hiveplane.budget.models import (
    CostAttribution,
    SpendByTeam,
    SpendByWorkload,
    SpendSummary,
)


def summarize_spend(attributions: Sequence[CostAttribution]) -> SpendSummary:
    """Roll up priced attributions by workload and by team.

    A workload's team is the first non-null team seen for it. Team-less
    attributions appear under their workload and are excluded from ``by_team``.
    Rows are ordered by descending total, then name.
    """
    workload_totals: dict[str, float] = {}
    workload_runs: dict[str, set[str]] = {}
    workload_teams: dict[str, str | None] = {}
    team_totals: dict[str, float] = {}
    team_runs: dict[str, set[str]] = {}

    for record in attributions:
        workload_totals[record.workload] = (
            workload_totals.get(record.workload, 0.0) + record.cost_usd
        )
        workload_runs.setdefault(record.workload, set()).add(record.run_id)
        if record.workload not in workload_teams or workload_teams[record.workload] is None:
            workload_teams[record.workload] = record.team
        if record.team is not None:
            team_totals[record.team] = team_totals.get(record.team, 0.0) + record.cost_usd
            team_runs.setdefault(record.team, set()).add(record.run_id)

    by_workload = [
        SpendByWorkload(
            workload=name,
            team=workload_teams[name],
            total_usd=total,
            run_count=len(workload_runs[name]),
        )
        for name, total in workload_totals.items()
    ]
    by_workload.sort(key=lambda row: (-row.total_usd, row.workload))

    by_team = [
        SpendByTeam(team=team, total_usd=total, run_count=len(team_runs[team]))
        for team, total in team_totals.items()
    ]
    by_team.sort(key=lambda row: (-row.total_usd, row.team))

    return SpendSummary(by_workload=by_workload, by_team=by_team)
