"""An in-memory ControlPlaneClient for UI tests (M22)."""

from __future__ import annotations

from typing import Any

from hiveplane.ui.client import ControlPlaneError


class FakeControlPlaneClient:
    """A scriptable client that records calls and returns preset payloads."""

    def __init__(self) -> None:
        self.workloads: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.story: dict[str, Any] = {}
        self.approvals: list[dict[str, Any]] = []
        self.certifications: list[dict[str, Any]] = []
        self.quarantines: list[dict[str, Any]] = []
        self.spend: dict[str, Any] = {"by_workload": [], "by_team": []}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.errors: dict[str, ControlPlaneError] = {}

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        error = self.errors.get(name)
        if error is not None:
            raise error

    def list_workloads(self) -> list[dict[str, Any]]:
        self._record("list_workloads")
        return self.workloads

    def get_workload(self, name: str) -> dict[str, Any]:
        self._record("get_workload", name)
        return next(w for w in self.workloads if w["name"] == name)

    def list_runs(
        self, workload: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        self._record("list_runs", workload, state)
        return self.runs

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._record("get_run", run_id)
        return next(r for r in self.runs if r["id"] == run_id)

    def get_story(self, run_id: str) -> dict[str, Any]:
        self._record("get_story", run_id)
        return self.story

    def list_approvals(
        self, status: str | None = None, workload: str | None = None
    ) -> list[dict[str, Any]]:
        self._record("list_approvals", status, workload)
        return self.approvals

    def approve(self, approval_id: str, operator: str, reason: str | None = None) -> dict[str, Any]:
        self._record("approve", approval_id, operator, reason)
        return {"approval_id": approval_id, "status": "approved"}

    def deny(self, approval_id: str, operator: str, reason: str | None = None) -> dict[str, Any]:
        self._record("deny", approval_id, operator, reason)
        return {"approval_id": approval_id, "status": "denied"}

    def pause(self, run_id: str) -> dict[str, Any]:
        self._record("pause", run_id)
        return {"id": run_id, "state": "paused"}

    def resume(self, run_id: str) -> dict[str, Any]:
        self._record("resume", run_id)
        return {"id": run_id, "state": "running"}

    def stop(self, run_id: str) -> dict[str, Any]:
        self._record("stop", run_id)
        return {"id": run_id, "state": "cancelled"}

    def record_feedback(
        self, run_id: str, verdict: str, notes: str, operator: str
    ) -> dict[str, Any]:
        self._record("record_feedback", run_id, verdict, notes, operator)
        return {"feedback_id": "fb-1", "run_id": run_id, "verdict": verdict}

    def list_certifications(
        self, workload: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        self._record("list_certifications", workload, status)
        return self.certifications

    def list_quarantines(self, workload: str | None = None) -> list[dict[str, Any]]:
        self._record("list_quarantines", workload)
        return self.quarantines

    def get_spend(self) -> dict[str, Any]:
        self._record("get_spend")
        return self.spend
