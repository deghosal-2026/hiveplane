"""Trajectory writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import append_trajectory


def _date_from_timestamp(timestamp: str) -> str:
    """Extract ``YYYY-MM-DD`` from an ISO timestamp, fallback to today UTC."""
    try:
        # Handle "2026-09-03T18:25:00Z" -> replace Z with +00:00 for fromisoformat.
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def trajectory_path(
    trajectory: Trajectory,
    base_dir: str | Path = "trajectories",
) -> Path:
    """Return the file path for *trajectory* under *base_dir*.

    Format: ``trajectories/YYYY-MM-DD/failure-T-{id}.jsonl`` or
    ``success-T-{id}.jsonl``.
    """
    date_str = _date_from_timestamp(trajectory.timestamp)
    prefix = "failure" if not trajectory.success else "success"
    # Sanitize id to filesystem-safe (alphanumeric + dash/underscore).
    safe_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in trajectory.id)
    filename = f"{prefix}-{safe_id}.jsonl"
    return Path(base_dir) / date_str / filename


def write_trajectory(
    trajectory: Trajectory,
    base_dir: str | Path = "trajectories",
) -> Path:
    """Write *trajectory* to its canonical JSONL file.

    Returns the path written to.
    """
    path = trajectory_path(trajectory, base_dir)
    append_trajectory(trajectory, path)
    return path
