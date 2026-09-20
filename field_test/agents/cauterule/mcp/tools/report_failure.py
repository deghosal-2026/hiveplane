"""MCP tool: ``report_failure`` — submit a failure trajectory for extraction."""

from __future__ import annotations

import json
from typing import Any

from cauterule.extraction.prompt import build_extraction_prompt
from cauterule.models.trajectory import Trajectory


def report_failure(trajectory_json: str) -> dict[str, Any]:
    """Accept a trajectory JSON and trigger extraction pipeline.

    The *trajectory_json* is handed to the extraction engine for analysis
    and candidate rule generation.

    Args:
        trajectory_json: JSON-serialised trajectory of the failed agent run.

    Returns:
        A dict with keys ``"accepted"`` (bool), ``"trajectory_length"``
        (int), and ``"message"`` (str).
    """
    try:
        data = json.loads(trajectory_json)
    except json.JSONDecodeError as exc:
        return {
            "accepted": False,
            "trajectory_length": 0,
            "message": f"Invalid JSON: {exc}",
        }

    if not isinstance(data, dict):
        return {
            "accepted": False,
            "trajectory_length": 0,
            "message": "Trajectory must be a JSON object",
        }

    steps = data.get("steps", []) if isinstance(data, dict) else []
    length = len(steps) if isinstance(steps, list) else 0

    try:
        traj = Trajectory.from_dict(data)
        prompt = build_extraction_prompt(traj)
    except Exception as exc:
        return {
            "accepted": False,
            "trajectory_length": length,
            "message": f"Trajectory construction failed ({length} steps): {exc}",
        }

    msg = (
        f"Trajectory accepted for extraction ({length} steps). "
        f"Prompt ({len(prompt)} chars) generated."
    )
    return {
        "accepted": True,
        "trajectory_length": length,
        "message": msg,
    }
