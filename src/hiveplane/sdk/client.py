"""HivePlane Python SDK: a typed client over the control-plane API v2 (M56-02)."""

from __future__ import annotations

import time
from typing import Any

import httpx

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class HivePlaneError(Exception):
    """Raised when a control-plane call fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"control plane error ({status_code}): {detail}")


class HivePlaneClient:
    """A synchronous client for the HivePlane control plane.

    Covers runs, certifications, triggers, pipelines, cost, approvals, and
    health. Pass a preconfigured ``httpx.Client`` to inject a transport (tests,
    custom auth, retries).
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        tenant_id: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if tenant_id:
            headers["X-Hiveplane-Tenant"] = tenant_id
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> HivePlaneClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        response = self._client.request(method, path, params=params, json=payload)
        if response.status_code >= 400:
            raise HivePlaneError(response.status_code, response.text)
        return response.json() if response.content else None

    # --- runs -------------------------------------------------------------
    def submit_run(
        self,
        workload: str,
        *,
        caller: str = "sdk",
        context: str = "production",
        task: dict[str, Any] | None = None,
        model_identity: str | None = None,
    ) -> dict[str, Any]:
        """Submit a run for a workload."""
        return self._request(  # type: ignore[no-any-return]
            "POST",
            "/runs",
            payload={
                "workload": workload,
                "caller": caller,
                "context": context,
                "task": task or {},
                "model_identity": model_identity,
            },
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Return a run."""
        return self._request("GET", f"/runs/{run_id}")  # type: ignore[no-any-return]

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return a page of runs from API v2."""
        params: dict[str, Any] = {"limit": limit}
        if workload:
            params["workload"] = workload
        if state:
            params["state"] = state
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v2/runs", params=params)  # type: ignore[no-any-return]

    def wait_for_run(
        self, run_id: str, *, timeout: float = 60.0, interval: float = 1.0
    ) -> dict[str, Any]:
        """Poll a run until it reaches a terminal state or the timeout elapses."""
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_run(run_id)
            if run.get("state") in _TERMINAL_STATES:
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(f"run {run_id} did not finish within {timeout}s")
            time.sleep(interval)

    def invoke_service(
        self, workload: str, *, task: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a workload through its agent-as-service endpoint."""
        return self._request(  # type: ignore[no-any-return]
            "POST", f"/services/{workload}/invoke", payload={"task": task or {}}
        )

    # --- registry & certification ----------------------------------------
    def list_workloads(self) -> list[dict[str, Any]]:
        """Return the fleet catalog."""
        return self._request("GET", "/workloads")  # type: ignore[no-any-return]

    def list_certifications(
        self, *, workload: str | None = None
    ) -> list[dict[str, Any]]:
        """Return certification records."""
        params = {"workload": workload} if workload else None
        return self._request("GET", "/certifications", params=params)  # type: ignore[no-any-return]

    # --- triggers, pipelines, cost, approvals, health --------------------
    def list_triggers(self) -> list[dict[str, Any]]:
        """Return configured triggers."""
        return self._request("GET", "/triggers")  # type: ignore[no-any-return]

    def list_pipelines(self) -> list[dict[str, Any]]:
        """Return configured pipelines."""
        return self._request("GET", "/pipelines")  # type: ignore[no-any-return]

    def get_showback(self, *, tenant_id: str = "default") -> dict[str, Any]:
        """Return a tenant's cost showback."""
        return self._request(  # type: ignore[no-any-return]
            "GET", "/cost/showback", params={"tenant_id": tenant_id}
        )

    def list_approvals(
        self, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Return approval requests."""
        params = {"status": status} if status else None
        return self._request("GET", "/approvals", params=params)  # type: ignore[no-any-return]

    def list_health(self) -> list[dict[str, Any]]:
        """Return fleet workload health."""
        return self._request("GET", "/health")  # type: ignore[no-any-return]
