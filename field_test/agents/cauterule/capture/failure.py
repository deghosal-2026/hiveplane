"""Failure point detection."""

from __future__ import annotations

from collections.abc import Sequence

from cauterule.models.trajectory import Step


def detect_failure_point(steps: Sequence[Step]) -> str | None:
    """Return the failure point identifier for *steps*.

    Returns ``step_{n}`` for the first step with a non-empty ``error``,
    or ``None`` if no step failed.
    """
    for step in steps:
        if step.error and step.error.strip():
            return f"step_{step.step_number}"
    return None


def detect_failure_class(steps: Sequence[Step]) -> str | None:
    """Classify failure class from the failing step.

    Heuristic: use tool name and error content to produce a taxonomy-like
    string (e.g. ``git/push/non-fast-forward`` is handled by taxonomy module,
    but this provides a basic fallback).
    """
    for step in steps:
        if step.error and step.error.strip():
            tool = step.tool.strip().lower()
            err = step.error.lower()
            if "non-fast-forward" in err or "rejected" in err:
                return "git/push/non-fast-forward"
            if "import" in err:
                return "python/import"
            if "docker" in tool or "docker" in err:
                return "docker/network"
            return f"{tool}/error"
    return None
