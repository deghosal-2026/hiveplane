"""Timeline builder — merges multi-source events into chronological order."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ..models import IncidentState, TimelineEvent

TRUST_MAP: dict[str, Literal["high", "medium", "low"]] = {
    # Alerting systems, chat platforms, and GitHub are trusted automated sources
    "alert": "high",
    "chat": "high",
    "github": "high",
    # Structured logs are trusted but may lack surrounding context
    "log": "medium",
    # Manual entries lack automated provenance, so trust them the least
    "manual": "low",
}
# TRUST_MAP drives tiebreaker sort order in _sort_key and the visual
# trust markers in format_timeline. Adding a new source requires an entry
# here; unknown sources default to a rank of 99 (lowest trust).


def build_timeline_node(state: IncidentState) -> IncidentState:
    """Merge events from all sources into a chronological timeline."""
    events: list[TimelineEvent] = []

    if state.alert:
        # Seed the timeline with the triggering alert event
        events.append(
            TimelineEvent(
                timestamp=state.alert.timestamp,
                source="alert",
                event_type="alert_fired",
                content=f"Alert: {state.alert.summary} (severity={state.alert.severity})",
                trust_level="high",
            )
        )

    for log in state.input_logs:
        # Add each log entry as a medium-trust event
        events.append(
            TimelineEvent(
                timestamp=log.timestamp,
                source="log",
                event_type=log.level.lower(),
                content=log.message,
                trust_level="medium",
            )
        )

    for msg in state.input_messages:
        # Add chat messages as high-trust events with author attribution
        events.append(
            TimelineEvent(
                timestamp=msg.timestamp,
                source="chat",
                event_type="message",
                content=f"[{msg.author}] {msg.text}",
                trust_level="high",
            )
        )

    for pr in state.input_prs:
        # Add each merged PR as a high-trust event for deploy correlation
        events.append(
            TimelineEvent(
                timestamp=pr.merge_time,
                source="github",
                event_type="pr_merged",
                content=f"PR #{pr.number} merged: {pr.title}",
                trust_level="high",
            )
        )

    # Append user-supplied events verbatim; their trust_level is already set
    manual_events = state.input_manual_events
    events.extend(manual_events)

    # Sort chronologically; ties broken by trust level (high < medium < low).
    # This ensures that when two events share the same timestamp, the more
    # trustworthy one appears first in the rendered timeline.
    events.sort(key=_sort_key)
    state.timeline = events
    return state


def _sort_key(event: TimelineEvent) -> tuple[datetime, int]:
    """Return (timestamp, trust_rank) for stable sort — higher trust sorts first."""
    # Lower rank sorts first: high=0, medium=1, low=2
    trust_order = {"high": 0, "medium": 1, "low": 2}
    # Tuple sort: primary=timestamp, secondary=trust rank as tiebreaker.
    # Unknown trust levels (99) sort last as a safety net.
    return (event.timestamp, trust_order.get(event.trust_level, 99))


def add_event(state: IncidentState, event: TimelineEvent) -> IncidentState:
    """Append a single event to the timeline, maintaining chronological order."""
    state.timeline.append(event)
    # Re-sort to keep the full timeline in chronological+trust order
    state.timeline.sort(key=_sort_key)
    return state


def format_timeline(timeline: list[TimelineEvent]) -> str:
    """Human-readable timeline for CLI display (SPEC §5.2)."""
    lines = []
    for event in timeline:
        # Trust markers make medium/low events visually distinct; high is unmarked.
        # Using dict lookup instead of if/elif to force a KeyError if a new trust
        # level is added to TimelineEvent but forgotten in format_timeline.
        trust_marker = {
            "high": "",
            "medium": " (trust: medium)",
            "low": " (trust: LOW — human-entered)",
        }[event.trust_level]
        # Deploy correlation flag is set by correlate_deploys_node
        deploy_marker = " [DEPLOY CORRELATION]" if event.deploy_correlation else ""
        lines.append(
            f"{event.timestamp.strftime('%H:%M:%S')} {event.content}"
            f"{trust_marker}{deploy_marker}"
        )
    return "\n".join(lines)


def get_timeline_summary(state: IncidentState) -> str:
    """Return a human-readable timeline summary."""
    if not state.timeline:
        return "No timeline events recorded."

    lines = []
    for evt in state.timeline:
        correlation = " [DEPLOY CORRELATION]" if evt.deploy_correlation else ""
        # ISO 8601 timestamp gives full precision and is machine-parseable
        lines.append(
            f"[{evt.timestamp.isoformat()}] "
            f"[{evt.source}] [{evt.trust_level}] "
            f"{evt.content}{correlation}"
        )
    return "\n".join(lines)
