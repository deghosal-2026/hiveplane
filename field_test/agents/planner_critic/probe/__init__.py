"""EnvProbe package (F-19, F-26): read-only environment probes.

See :mod:`planner_critic.probe.base` for the protocol and built-ins. Probes
observe live state, never mutate it, and record results for the execution
trace.
"""

from __future__ import annotations

from .base import (
    BUILTIN_PROBES,
    Probe,
    ProbeError,
    ProbeKind,
    ProbeRequest,
    ProbeResult,
    run_probe,
)

__all__ = [
    "BUILTIN_PROBES",
    "Probe",
    "ProbeError",
    "ProbeKind",
    "ProbeRequest",
    "ProbeResult",
    "run_probe",
]
