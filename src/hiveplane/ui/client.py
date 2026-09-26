"""HTTP client for the control-plane REST API (M22, #84).

The operator UI never imports the control plane's services; it speaks to the
JSON API. This module is that boundary: a small synchronous client plus the
error type the UI renders when the control plane is unavailable.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx


class ControlPlaneError(Exception):
    """Raised when a control-plane call fails or is unreachable."""

    def __init__(self, status_code: int | None, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"control plane error ({status_code}): {detail}")


class ControlPlaneClient(Protocol):
    """The control-plane API surface the operator UI depends on."""

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

    def list_certifications(
        self, workload: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    def list_quarantines(
        self, workload: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_spend(self) -> dict[str, Any]: ...


class HttpControlPlaneClient:
    """A synchronous :class:`ControlPlaneClient` over HTTP."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

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
        try:
            response = self._client.request(
                method, f"{self.base_url}{path}", params=params, json=payload
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


def _detail(response: httpx.Response) -> str:
    """Extract a FastAPI ``{"detail": ...}`` body, falling back to text."""
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)
