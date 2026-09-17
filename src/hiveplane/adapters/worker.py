"""The worker-facing control-plane client (M16).

A workload entrypoint receives a :class:`WorkerContext` that routes tool calls
through the policy boundary, reports usage, and offers cooperative pause/cancel
checkpoints. Workers report; the control plane decides.
"""

from __future__ import annotations

import threading

from hiveplane.adapters.errors import RunCancelledError
from hiveplane.core.run import RunState


class RunControl:
    """Cooperative pause/cancel signaling for a live run."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._cancelled = False

    @property
    def paused(self) -> bool:
        """Return True while the run is paused."""
        with self._condition:
            return self._paused

    @property
    def cancelled(self) -> bool:
        """Return True once the run has been cancelled."""
        with self._condition:
            return self._cancelled

    def pause(self) -> None:
        """Request a cooperative pause."""
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        """Clear a pause request and wake any blocked checkpoint."""
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        """Request cancellation and wake any blocked checkpoint."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def checkpoint(self) -> None:
        """Block while paused, raising when the run has been cancelled."""
        with self._condition:
            while self._paused and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RunCancelledError(RunState.CANCELLED)
