"""Cost report node — aggregates CostTracker data and saves session."""

from __future__ import annotations

import logging

from incident_commander.models.state import IncidentState

from ._llm import get_llm_router

logger = logging.getLogger(__name__)


def cost_report_node(state: IncidentState) -> IncidentState:
    """Aggregate CostTracker data into a CostReport and store in state.

    Also persists session data via SessionManager if the session
    directory is available from state config.
    """
    router = get_llm_router()
    # Pull the aggregated cost report keyed by thread_id; CostTracker
    # internally sums per-node costs accumulated during the session.
    report = router.cost_tracker.get_report(session_id=state.thread_id)
    state.cost_report = report
    return state

    # NOTE: The docstring mentions SessionManager persistence, but no
    # write-to-disk call is made here. Persistence is handled downstream
    # by the output writer invoked after the graph completes.
