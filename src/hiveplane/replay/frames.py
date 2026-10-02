"""Frame-by-frame reconstruction of a run from its events and usage (M60-01).

A frame set is a deterministic, side-effect-free projection of a run: the ordered
event log with each ``usage`` event carrying its matching :class:`UsageReport`.
The same records always produce the same digest, so a replay can be verified
without re-executing anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import Run
from hiveplane.core.usage import UsageReport
from hiveplane.replay.models import ReplayFrame, ReplayFrameSet


def build_frames(
    run: Run,
    events: Sequence[RunEvent],
    usage: Sequence[UsageReport],
) -> ReplayFrameSet:
    """Reconstruct a run's execution frames from its persisted records."""
    reports = list(usage)
    frames: list[ReplayFrame] = []
    matched = 0
    for event in sorted(events, key=lambda item: item.sequence):
        report: UsageReport | None = None
        if event.type is EventType.USAGE and matched < len(reports):
            report = reports[matched]
            matched += 1
        frames.append(
            ReplayFrame(
                sequence=len(frames),
                timestamp=event.timestamp,
                event_type=event.type.value,
                actor=event.actor,
                from_state=event.from_state,
                to_state=event.to_state,
                detail=event.detail,
                usage=report,
            )
        )
    for report in reports[matched:]:
        frames.append(
            ReplayFrame(
                sequence=len(frames),
                timestamp=report.timestamp,
                event_type="model_call",
                actor=run.caller,
                usage=report,
            )
        )
    return ReplayFrameSet(
        run_id=run.id,
        frames=frames,
        frame_count=len(frames),
        digest=_digest(frames),
        side_effects=False,
    )


def _digest(frames: Sequence[ReplayFrame]) -> str:
    payload = [frame.model_dump(mode="json") for frame in frames]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
