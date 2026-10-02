"""Drift, quarantine, and reinstatement errors (M34)."""

from __future__ import annotations


class DriftError(Exception):
    """Base class for drift-detection and quarantine errors."""


class QuarantineNotFoundError(DriftError):
    """Raised when no quarantine record exists for a workload."""

    def __init__(self, workload: str) -> None:
        self.workload = workload
        super().__init__(f"no quarantine record for workload {workload!r}")


class ReinstatementRefusedError(DriftError):
    """Raised when a workload cannot be reinstated without a fresh certification."""

    def __init__(self, workload: str, reason: str) -> None:
        self.workload = workload
        self.reason = reason
        super().__init__(f"reinstatement of {workload!r} refused: {reason}")


class DriftNotConfiguredError(DriftError):
    """Raised when drift evaluation is requested without a usable baseline."""

    def __init__(self, workload: str, reason: str) -> None:
        self.workload = workload
        self.reason = reason
        super().__init__(f"drift evaluation for {workload!r} unavailable: {reason}")
