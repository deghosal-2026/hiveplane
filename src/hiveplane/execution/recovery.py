"""Startup recovery for runs interrupted by a control-plane restart (M23, #111).

Design D18. On boot the control plane reconciles every non-terminal run left in
the run store by a previous process:

- **paused** runs are re-attached to their runtime adapter so an operator can
  resume them; the adapter (and, for LangGraph, the durable checkpointer) holds
  the state needed to continue.
- **running** runs lost their in-process worker with the old process, so they
  are reconciled to ``failed`` with reason ``interrupted`` rather than sitting in
  ``running`` forever.

Recovery is safe to run on every boot: terminal and queued runs are untouched,
and a reconciled run is skipped on the next pass.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.event import EventType
from hiveplane.core.run import RunState
from hiveplane.execution.service import RunService
from hiveplane.registry.errors import WorkloadNotFoundError

_INTERRUPTED_REASON = "interrupted by control-plane restart"


class RecoveryReport(BaseModel):
    """The outcome of one recovery pass."""

    model_config = ConfigDict(extra="forbid")

    reattached: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class RunRecovery:
    """Reconciles non-terminal runs when the control plane boots."""

    def __init__(self, run_service: RunService) -> None:
        self._run_service = run_service

    def run(self) -> RecoveryReport:
        """Scan the run store and reconcile paused and running runs."""
        report = RecoveryReport()
        for run in self._run_service.list_runs():
            try:
                if run.state is RunState.PAUSED:
                    self._reattach(run.id, report)
                elif run.state is RunState.RUNNING:
                    self._fail(run.id, report)
            except WorkloadNotFoundError:
                report.skipped.append(run.id)
        return report

    def _reattach(self, run_id: str, report: RecoveryReport) -> None:
        if not self._run_service.reattach(run_id):
            return
        self._run_service.record_event(
            run_id,
            EventType.RECOVERY,
            "recovery",
            detail="reattached after control-plane restart",
        )
        report.reattached.append(run_id)

    def _fail(self, run_id: str, report: RecoveryReport) -> None:
        self._run_service.fail(run_id, actor="recovery", reason=_INTERRUPTED_REASON)
        self._run_service.record_event(
            run_id,
            EventType.RECOVERY,
            "recovery",
            detail="interrupted run reconciled to failed",
        )
        report.failed.append(run_id)
