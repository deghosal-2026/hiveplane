"""Cadence timer — determines next stakeholder update time based on severity.

The cadence is configurable via ``config.cadence`` and defaults to:
- SEV1 → 5 minutes
- SEV2 → 15 minutes
- SEV3 → 30 minutes
"""

from __future__ import annotations

from datetime import datetime, timedelta

from incident_commander.config import Config
from incident_commander.models.state import IncidentState

# Default cadence if config is not provided
_DEFAULT_CADENCE: dict[str, int] = {"SEV1": 5, "SEV2": 15, "SEV3": 30}

# Module-level config — set once by the graph builder
_config: Config | None = None


def init_config(config: Config) -> None:
    """Set the module-level config used by ``cadence_timer_node``."""
    global _config  # noqa: PLW0603
    _config = config


def _get_cadence_minutes(severity: str) -> int:
    """Return the cadence interval (minutes) for the given severity."""
    if _config is not None:
        # Config's cadence dict uses the same severity-key scheme; unknown
        # severity falls through to 30 minutes as a safe default.
        return _config.cadence.get(severity, 30)
    return _DEFAULT_CADENCE.get(severity, 30)


def cadence_timer_node(state: IncidentState) -> IncidentState:
    """Set ``state.next_update_time`` based on incident severity.

    Uses ``state.last_update_time`` (or ``state.alert.timestamp``) as the
    base from which to add the cadence interval.  Unknown severity
    defaults to 30 minutes.
    """
    minutes = _get_cadence_minutes(state.severity)

    # Fallback chain: last sent update → alert trigger time → current time.
    # Using alert.timestamp ensures cadence is relative to when the incident
    # started, not when the node happens to execute.
    base = state.last_update_time
    if base is None and state.alert is not None:
        base = state.alert.timestamp
    if base is None:
        # Sink fallback for simulate mode where alert may be absent during
        # testing; uses wall clock rather than failing.
        base = datetime.now()

    state.next_update_time = base + timedelta(minutes=minutes)
    return state
