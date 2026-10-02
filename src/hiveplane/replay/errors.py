"""Errors for the replay service (M60)."""

from __future__ import annotations


class ReplayError(Exception):
    """Base class for replay failures."""


class ReplayNotFoundError(ReplayError):
    """Raised when a replay record is absent or out of tenant scope."""

    def __init__(self, replay_id: str) -> None:
        super().__init__(f"replay {replay_id!r} not found")
        self.replay_id = replay_id
