"""Build the chaos engine with default drills from live services (M48)."""

from __future__ import annotations

from typing import Any

from hiveplane.chaos.engine import ChaosEngine, DrillOutcome
from hiveplane.chaos.models import DrillKind, DrillRequest


def build_chaos_engine(
    *,
    worker_registry: Any | None = None,
    kill_switch: Any | None = None,
    clock: Any | None = None,
) -> ChaosEngine:
    """Build a chaos engine wired to the plane's worker registry and kill switch."""
    drills: dict[DrillKind, Any] = {}

    if worker_registry is not None:

        def kill_worker(request: DrillRequest) -> DrillOutcome:
            unhealthy = worker_registry.mark_unhealthy()
            reassigned = worker_registry.reclaim()
            observed = (
                f"marked {len(unhealthy)} worker(s) unhealthy; "
                f"reassigned {len(reassigned)} lease(s)"
            )
            return DrillOutcome(observed, True)

        drills[DrillKind.KILL_WORKER] = kill_worker

    if kill_switch is not None:

        def inject_tool_failure(request: DrillRequest) -> DrillOutcome:
            kill_switch.disable(
                request.scope_ref, actor="chaos", reason="seeded tool failure"
            )
            return DrillOutcome(f"disabled tool {request.scope_ref!r}", True)

        drills[DrillKind.INJECT_TOOL_FAILURE] = inject_tool_failure

    return ChaosEngine(drills, clock=clock)
