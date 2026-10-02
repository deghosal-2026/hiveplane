"""An in-memory ControlPlaneClient for UI tests (M22)."""

from __future__ import annotations

from typing import Any

from hiveplane.config import get_settings
from hiveplane.tenancy.models import Role
from hiveplane.ui.client import ControlPlaneError
from hiveplane.ui.session import UiSession, sign_session


def session_token(
    role: Role,
    *,
    operator_id: str = "alice",
    tenant_id: str = "default",
    api_key: str = "hp-key-1",
) -> str:
    """Mint a signed session cookie value for UI tests."""
    session = UiSession(
        operator_id=operator_id,
        tenant_id=tenant_id,
        role=role,
        api_key=api_key,
        exp=9_999_999_999,
    )
    return sign_session(session, get_settings().ui.session_secret)


class FakeControlPlaneClient:
    """A scriptable client that records calls and returns preset payloads."""

    def __init__(self) -> None:
        self.workloads: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.story: dict[str, Any] = {}
        self.stories: dict[str, dict[str, Any]] = {}
        self.approvals: list[dict[str, Any]] = []
        self.certifications: list[dict[str, Any]] = []
        self.quarantines: list[dict[str, Any]] = []
        self.spend: dict[str, Any] = {"by_workload": [], "by_team": []}
        self.queue: dict[str, Any] = {"running": [], "pending": []}
        self.health: list[dict[str, Any]] = []
        self.roi: dict[str, Any] = {}
        self.search_hits: list[dict[str, Any]] = []
        self.triggers: list[dict[str, Any]] = []
        self.adapters: list[dict[str, Any]] = []
        self.version_diff: dict[str, Any] = {}
        self.cert_comparison: dict[str, Any] = {}
        self.run_events: list[dict[str, Any]] = []
        self.replay: dict[str, Any] = {}
        self.replay_diff_result: dict[str, Any] = {}
        self.artifacts: list[dict[str, Any]] = []
        self.fleet_state_data: dict[str, Any] = {
            "halted": False,
            "active": None,
            "history": [],
        }
        self.identity: dict[str, Any] = {
            "operator_id": "anonymous",
            "tenant_id": "default",
            "role": "admin",
            "method": "api_key",
            "scopes": [],
        }
        self.token: str | None = None
        self.tenant_id: str | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.errors: dict[str, ControlPlaneError] = {}

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        error = self.errors.get(name)
        if error is not None:
            raise error

    def with_token(
        self, token: str | None, *, tenant_id: str | None = None
    ) -> FakeControlPlaneClient:
        self._record("with_token", token)
        self.token = token
        self.tenant_id = tenant_id
        return self

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
        return self.stories.get(run_id, self.story)

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

    def fleet_state(self) -> dict[str, Any]:
        self._record("fleet_state")
        return self.fleet_state_data

    def pause_fleet(
        self, actor: str, reason: str | None = None
    ) -> dict[str, Any]:
        self._record("pause_fleet", actor, reason)
        return {"incident_id": "inc-1", "actor": actor, "resumed_at": None}

    def resume_fleet(
        self, actor: str, incident_id: str | None = None
    ) -> dict[str, Any]:
        self._record("resume_fleet", actor, incident_id)
        return {"incident_id": incident_id or "inc-1", "resumed_by": actor}

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

    def get_queue(self) -> dict[str, Any]:
        self._record("get_queue")
        return self.queue

    def list_health(self) -> list[dict[str, Any]]:
        self._record("list_health")
        return self.health

    def get_roi(self) -> dict[str, Any]:
        self._record("get_roi")
        return self.roi

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        self._record("search", query, limit)
        return self.search_hits

    def list_triggers(self) -> list[dict[str, Any]]:
        self._record("list_triggers")
        return self.triggers

    def list_adapters(self) -> list[dict[str, Any]]:
        self._record("list_adapters")
        return self.adapters

    def get_version_diff(
        self, name: str, from_version: int, to_version: int
    ) -> dict[str, Any]:
        self._record("get_version_diff", name, from_version, to_version)
        return self.version_diff

    def compare_certifications(self, before_id: str, after_id: str) -> dict[str, Any]:
        self._record("compare_certifications", before_id, after_id)
        return self.cert_comparison

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        self._record("get_run_events", run_id)
        return self.run_events

    def get_replay(self, run_id: str) -> dict[str, Any]:
        self._record("get_replay", run_id)
        return self.replay

    def replay_diff(self, before_id: str, after_id: str) -> dict[str, Any]:
        self._record("replay_diff", before_id, after_id)
        return self.replay_diff_result

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        self._record("list_artifacts", run_id)
        return self.artifacts

    def add_approval_comment(
        self, approval_id: str, author: str, text: str
    ) -> dict[str, Any]:
        self._record("add_approval_comment", approval_id, author, text)
        return {"approval_id": approval_id, "author": author, "text": text}

    def delegate_approval(self, approval_id: str, assignee: str, by: str) -> dict[str, Any]:
        self._record("delegate_approval", approval_id, assignee, by)
        return {"approval_id": approval_id, "assignee": assignee}

    def whoami(self) -> dict[str, Any]:
        self._record("whoami")
        return self.identity
