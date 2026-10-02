"""Server-Sent Events for the live run view (M52-02)."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

STREAM_MAX_SECONDS = 300.0
STREAM_POLL_SECONDS = 1.0


def _sequence(event: dict[str, Any]) -> int:
    """Parse an event's sequence, treating null or non-numeric values as 0."""
    try:
        return int(event.get("sequence", 0))
    except (TypeError, ValueError):
        return 0


def run_event_frames(events: Sequence[dict[str, Any]], *, seen: int) -> Iterator[str]:
    """Yield SSE frames for events beyond ``seen``, ordered by sequence."""
    ordered = sorted(events, key=_sequence)
    for event in ordered:
        if _sequence(event) <= seen:
            continue
        yield f"event: run\ndata: {json.dumps(event)}\n\n"
