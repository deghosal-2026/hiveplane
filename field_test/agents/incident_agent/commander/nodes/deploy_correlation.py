"""Deploy correlation — correlate GitHub PRs with alert timestamp (SPEC §6.1)."""

from __future__ import annotations

from ..models import DeployCorrelation, IncidentState

# Default correlation window used until state.config is wired in S4
# PRs merged within this many minutes before the alert are candidates
DEFAULT_WINDOW_MINUTES = 30
# PRs merged ≤ this many minutes before alert are "strong"; beyond are "weak"
STRONG_THRESHOLD_MINUTES = 15


def correlate_deploys_node(state: IncidentState) -> IncidentState:
    """Correlate GitHub PRs/commits with the alert timestamp.

    PRs merged strictly before the alert within the configured window are flagged.
    - strong: ≤ 15 min before alert
    - weak: ≤ window minutes before alert
    """
    if not state.alert:
        # No alert means nothing to correlate PRs against
        return state

    alert_time = state.alert.timestamp
    prs = state.input_prs
    if not prs:
        # No PRs to evaluate; skip correlation entirely
        return state

    # TODO S4: read from state.config.deploy_correlation_window_minutes
    window_minutes = DEFAULT_WINDOW_MINUTES

    correlations: list[DeployCorrelation] = []

    for pr in prs:
        # Minutes between PR merge and alert (positive = PR merged before alert)
        time_diff = (alert_time - pr.merge_time).total_seconds() / 60

        # Strictly positive: PR must merge BEFORE the alert.
        # Zero (same instant) and negative (after alert) are excluded to
        # avoid false causality (PR merged during/after incident response).
        if 0 < time_diff <= window_minutes:
            # ≤ STRONG_THRESHOLD_MINUTES = strong; otherwise weak
            strength = "strong" if time_diff <= STRONG_THRESHOLD_MINUTES else "weak"
            correlations.append(
                DeployCorrelation(
                    pr_number=pr.number,
                    pr_title=pr.title,
                    author=pr.author,
                    merge_time=pr.merge_time,
                    files_changed=pr.files_changed,
                    minutes_before_alert=int(time_diff),
                    correlation_strength=strength,  # type: ignore[arg-type]
                )
            )

            # Flag matching timeline events so format_timeline can render
            # a [DEPLOY CORRELATION] marker. Match is on exact merge_time
            # — assumes timeline entries for PRs were already created by
            # build_timeline_node with the same timestamp.
            for event in state.timeline:
                if (
                    event.source == "github"
                    and pr.merge_time == event.timestamp
                ):
                    event.deploy_correlation = True

    state.deploy_correlations = correlations
    return state
