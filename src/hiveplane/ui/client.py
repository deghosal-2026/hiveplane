"""HTTP client for the control-plane REST API (M22, #84).

The operator UI never imports the control plane's services; it speaks to the
JSON API. This module is that boundary: a small synchronous client plus the
error type the UI renders when the control plane is unavailable.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx

_TENANT_HEADER = "X-Hiveplane-Tenant"


class ControlPlaneError(Exception):
    """Raised when a control-plane call fails or is unreachable."""

    def __init__(self, status_code: int | None, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"control plane error ({status_code}): {detail}")


class ControlPlaneClient(Protocol):
    """The control-plane API surface the operator UI depends on."""

    def with_token(
        self, token: str | None, *, tenant_id: str | None = None
    ) -> ControlPlaneClient:
        """Return a clone that forwards ``token`` as a bearer credential."""
        ...

    def list_workloads(self) -> list[dict[str, Any]]: ...

    def get_workload(self, name: str) -> dict[str, Any]: ...

    def list_runs(
        self, workload: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def get_story(self, run_id: str) -> dict[str, Any]: ...

    def list_approvals(
        self, status: str | None = None, workload: str | None = None
    ) -> list[dict[str, Any]]: ...

    def approve(
        self, approval_id: str, operator: str, reason: str | None = None
    ) -> dict[str, Any]: ...

    def deny(
        self, approval_id: str, operator: str, reason: str | None = None
    ) -> dict[str, Any]: ...

    def pause(self, run_id: str) -> dict[str, Any]: ...
    def resume(self, run_id: str) -> dict[str, Any]: ...

    def stop(self, run_id: str) -> dict[str, Any]: ...

    def fleet_state(self) -> dict[str, Any]: ...

    def pause_fleet(self, actor: str, reason: str | None = None) -> dict[str, Any]: ...

    def resume_fleet(
        self, actor: str, incident_id: str | None = None
    ) -> dict[str, Any]: ...

    def record_feedback(
        self, run_id: str, verdict: str, notes: str, operator: str
    ) -> dict[str, Any]: ...

    def list_certifications(
        self, workload: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    def list_quarantines(
        self, workload: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_spend(self) -> dict[str, Any]: ...

    def get_queue(self) -> dict[str, Any]: ...

    def list_health(self) -> list[dict[str, Any]]: ...

    def get_roi(self) -> dict[str, Any]: ...

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]: ...

    def list_triggers(self) -> list[dict[str, Any]]: ...

    def list_adapters(self) -> list[dict[str, Any]]: ...

    def get_version_diff(
        self, name: str, from_version: int, to_version: int
    ) -> dict[str, Any]: ...

    def compare_certifications(
        self, before_id: str, after_id: str
    ) -> dict[str, Any]: ...

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]: ...

    def get_replay(self, run_id: str) -> dict[str, Any]: ...

    def replay_diff(self, before_id: str, after_id: str) -> dict[str, Any]: ...

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]: ...

    def add_approval_comment(
        self, approval_id: str, author: str, text: str
    ) -> dict[str, Any]: ...

    def delegate_approval(
        self, approval_id: str, assignee: str, by: str
    ) -> dict[str, Any]: ...

    def whoami(self) -> dict[str, Any]: ...


class HttpControlPlaneClient:
    """A synchronous :class:`ControlPlaneClient` over HTTP."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        token: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._token = token
        self._tenant = tenant_id

    def with_token(
        self, token: str | None, *, tenant_id: str | None = None
    ) -> HttpControlPlaneClient:
        """Return a clone that sends the bearer token and acting tenant, if any."""
        clone = HttpControlPlaneClient.__new__(HttpControlPlaneClient)
        clone.base_url = self.base_url
        clone._client = self._client
        clone._token = token
        clone._tenant = tenant_id
        return clone

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._tenant:
            headers[_TENANT_HEADER] = self._tenant
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=payload,
                headers=headers or None,
            )
        except httpx.HTTPError as exc:
            raise ControlPlaneError(None, str(exc)) from exc
        if response.status_code >= 400:
            raise ControlPlaneError(response.status_code, _detail(response))
        if not response.content:
            return None
        return response.json()

    @staticmethod
    def _filters(**items: str | None) -> dict[str, str]:
        return {key: value for key, value in items.items() if value is not None}

    def list_workloads(self) -> list[dict[str, Any]]:
        """Return the fleet catalog."""
        return self._request("GET", "/workloads")  # type: ignore[no-any-return]

    def get_workload(self, name: str) -> dict[str, Any]:
        """Return one workload's registered state."""
        return self._request("GET", f"/workloads/{quote(name)}")  # type: ignore[no-any-return]

    def list_runs(
        self, workload: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        """Return runs, optionally filtered."""
        params = self._filters(workload=workload, state=state)
        return self._request("GET", "/runs", params=params)  # type: ignore[no-any-return]

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Return one run."""
        return self._request("GET", f"/runs/{quote(run_id)}")  # type: ignore[no-any-return]

    def get_story(self, run_id: str) -> dict[str, Any]:
        """Return a run's execution story."""
        return self._request("GET", f"/runs/{quote(run_id)}/story")  # type: ignore[no-any-return]

    def list_approvals(
        self, status: str | None = None, workload: str | None = None
    ) -> list[dict[str, Any]]:
        """Return approval requests, optionally filtered."""
        params = self._filters(status=status, workload=workload)
        return self._request("GET", "/approvals", params=params)  # type: ignore[no-any-return]

    def approve(
        self, approval_id: str, operator: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Approve a request."""
        payload: dict[str, Any] = {"operator": operator}
        if reason is not None:
            payload["reason"] = reason
        return self._request(  # type: ignore[no-any-return]
            "POST", f"/approvals/{quote(approval_id)}/approve", payload=payload
        )

    def deny(
        self, approval_id: str, operator: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Deny a request."""
        payload: dict[str, Any] = {"operator": operator}
        if reason is not None:
            payload["reason"] = reason
        return self._request(  # type: ignore[no-any-return]
            "POST", f"/approvals/{quote(approval_id)}/deny", payload=payload
        )

    def pause(self, run_id: str) -> dict[str, Any]:
        """Pause a run."""
        return self._request("POST", f"/runs/{quote(run_id)}/pause")  # type: ignore[no-any-return]

    def resume(self, run_id: str) -> dict[str, Any]:
        """Resume a run."""
        return self._request("POST", f"/runs/{quote(run_id)}/resume")  # type: ignore[no-any-return]

    def stop(self, run_id: str) -> dict[str, Any]:
        """Stop a run."""
        return self._request("POST", f"/runs/{quote(run_id)}/stop")  # type: ignore[no-any-return]

    def fleet_state(self) -> dict[str, Any]:
        """Return the incident-mode halt state and history."""
        return self._request("GET", "/fleet/state")  # type: ignore[no-any-return]

    def pause_fleet(self, actor: str, reason: str | None = None) -> dict[str, Any]:
        """Halt the fleet in incident mode."""
        payload: dict[str, Any] = {"actor": actor, "trigger": "operator"}
        if reason is not None:
            payload["reason"] = reason
        return self._request(  # type: ignore[no-any-return]
            "POST", "/fleet/pause", payload=payload
        )

    def resume_fleet(
        self, actor: str, incident_id: str | None = None
    ) -> dict[str, Any]:
        """Lift the fleet halt with attribution."""
        payload: dict[str, Any] = {"actor": actor}
        if incident_id is not None:
            payload["incident_id"] = incident_id
        return self._request(  # type: ignore[no-any-return]
            "POST", "/fleet/resume", payload=payload
        )

    def record_feedback(
        self, run_id: str, verdict: str, notes: str, operator: str
    ) -> dict[str, Any]:
        """Record operator feedback on a terminal run."""
        payload = {"verdict": verdict, "notes": notes, "operator": operator}
        return self._request(  # type: ignore[no-any-return]
            "POST", f"/runs/{quote(run_id)}/feedback", payload=payload
        )

    def list_certifications(
        self, workload: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Return certification records, optionally filtered."""
        params = self._filters(workload=workload, status=status)
        return self._request("GET", "/certifications", params=params)  # type: ignore[no-any-return]

    def list_quarantines(self, workload: str | None = None) -> list[dict[str, Any]]:
        """Return persisted quarantine history, optionally filtered (M34-05)."""
        params = self._filters(workload=workload)
        return self._request("GET", "/quarantines", params=params)  # type: ignore[no-any-return]

    def get_spend(self) -> dict[str, Any]:
        """Return attributed spend by workload and team."""
        return self._request("GET", "/spend")  # type: ignore[no-any-return]

    def get_queue(self) -> dict[str, Any]:
        """Return the scheduler queue snapshot."""
        return self._request("GET", "/queue")  # type: ignore[no-any-return]

    def list_health(self) -> list[dict[str, Any]]:
        """Return fleet workload health."""
        return self._request("GET", "/health")  # type: ignore[no-any-return]

    def get_roi(self) -> dict[str, Any]:
        """Return fleet-wide return on investment."""
        return self._request("GET", "/cost/roi/fleet")  # type: ignore[no-any-return]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search workloads and runs by free-text query."""
        return self._request(  # type: ignore[no-any-return]
            "GET", "/search", params={"q": query, "limit": str(limit)}
        )

    def list_triggers(self) -> list[dict[str, Any]]:
        """Return configured triggers."""
        return self._request("GET", "/triggers")  # type: ignore[no-any-return]

    def list_adapters(self) -> list[dict[str, Any]]:
        """Return registered adapter metadata."""
        return self._request("GET", "/adapters")  # type: ignore[no-any-return]

    def get_version_diff(
        self, name: str, from_version: int, to_version: int
    ) -> dict[str, Any]:
        """Return the diff between two workload versions."""
        return self._request(  # type: ignore[no-any-return]
            "GET",
            f"/workloads/{quote(name)}/versions/diff",
            params={"from_version": str(from_version), "to_version": str(to_version)},
        )

    def compare_certifications(self, before_id: str, after_id: str) -> dict[str, Any]:
        """Return the regression diff between two certification records."""
        return self._request(  # type: ignore[no-any-return]
            "GET", f"/certifications/compare/{quote(before_id)}/{quote(after_id)}"
        )

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Return the event log for a run."""
        return self._request("GET", f"/runs/{quote(run_id)}/events")  # type: ignore[no-any-return]

    def get_replay(self, run_id: str) -> dict[str, Any]:
        """Reconstruct a run frame-by-frame through the replay service."""
        return self._request(  # type: ignore[no-any-return]
            "POST", f"/replay/{quote(run_id)}"
        )

    def replay_diff(self, before_id: str, after_id: str) -> dict[str, Any]:
        """Return the replay run-to-run diff between two runs."""
        return self._request(  # type: ignore[no-any-return]
            "GET",
            "/replay/diff",
            params={"run_a": before_id, "run_b": after_id},
        )

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Return the artifacts linked to a run."""
        return self._request(  # type: ignore[no-any-return]
            "GET", "/artifacts", params={"run_id": run_id}
        )

    def add_approval_comment(
        self, approval_id: str, author: str, text: str
    ) -> dict[str, Any]:
        """Append a comment to an approval request."""
        return self._request(  # type: ignore[no-any-return]
            "POST",
            f"/approvals/{quote(approval_id)}/comments",
            payload={"author": author, "text": text},
        )

    def delegate_approval(self, approval_id: str, assignee: str, by: str) -> dict[str, Any]:
        """Reassign an approval request to another operator."""
        return self._request(  # type: ignore[no-any-return]
            "POST",
            f"/approvals/{quote(approval_id)}/delegate",
            payload={"assignee": assignee, "operator": by},
        )

    def whoami(self) -> dict[str, Any]:
        """Return the identity the control plane associates with the token."""
        return self._request("GET", "/auth/whoami")  # type: ignore[no-any-return]


def _detail(response: httpx.Response) -> str:
    """Extract a FastAPI ``{"detail": ...}`` body, falling back to text."""
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)
