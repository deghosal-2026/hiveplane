"""Drift detection, auto-quarantine, and reinstatement (M34).

Detects behavioral drift of a certified workload against its certified baseline,
auto-quarantines on sustained or strong drift, notifies the owner with evidence,
persists quarantine history, and provides a certified-only reinstatement path.
"""

from __future__ import annotations

from hiveplane.drift.detector import DriftDetector
from hiveplane.drift.errors import (
    DriftError,
    DriftNotConfiguredError,
    QuarantineNotFoundError,
    ReinstatementRefusedError,
)
from hiveplane.drift.models import (
    CertificationExpiry,
    DriftAssessment,
    DriftSchedule,
    DriftVerdict,
    DueWorkload,
    ExpiryState,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.drift.store import (
    DriftStore,
    InMemoryDriftStore,
    PostgresDriftStore,
    build_drift_store,
)

__all__ = [
    "CertificationExpiry",
    "DriftAssessment",
    "DriftDetector",
    "DriftError",
    "DriftMonitor",
    "DriftNotConfiguredError",
    "DriftSchedule",
    "DriftScheduler",
    "DriftStore",
    "DriftVerdict",
    "DueWorkload",
    "ExpiryState",
    "InMemoryDriftStore",
    "PostgresDriftStore",
    "QuarantineNotFoundError",
    "QuarantineRecord",
    "QuarantineService",
    "QuarantineStatus",
    "ReinstatementRefusedError",
    "ReinstatementService",
    "build_drift_store",
]
