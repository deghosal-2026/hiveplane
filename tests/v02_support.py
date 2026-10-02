"""Shared helpers for the v0.2.0 container-layer suite (M61).

Every v0.2.0 docker test module imports from here so request shapes, the local
model identity, run polling, approval auto-resume, and tenant headers stay
consistent. Like the v0.1.0 suite these run against the live compose stack and
never skip on a missing model (see ``tests/docker/conftest.py``).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
WORKLOADS_DIR = ROOT / "examples" / "workloads"
FIELD_WORKLOADS_DIR = ROOT / "field_test" / "workloads"
ENV_FILE = ROOT / ".env.local"
TERMINAL = {"completed", "failed", "cancelled"}

#: The local model the stack certifies and binds runs to (no skips if absent).
DEFAULT_IDENTITY = "omlx/qwen3-4b-instruct-2507/4bit"

TENANT_HEADER = "X-Hiveplane-Tenant"
TEAM_HEADER = "X-Hiveplane-Team"

#: A deterministic support-agent task that completes (does not escalate).
TASK_OK: dict[str, Any] = {"query": "reset password", "account_id": "ACC-001"}
#: A deterministic support-agent task that escalates through the destructive tool.
TASK_ESCALATE: dict[str, Any] = {
    "query": "totally unknown topic",
    "account_id": "ACC-001",
}


def canonical_identity() -> str:
    """Return the canonical model identity the stack binds runs to."""
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("HIVEPLANE_MODEL__DEFAULT_MODEL="):
                return line.partition("=")[2].strip()
    return DEFAULT_IDENTITY


def request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
    tenant: str | None = None,
    team: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """Send a JSON request to the live control plane; return (status, body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    all_headers = {"Content-Type": "application/json"} if data is not None else {}
    if tenant:
        all_headers[TENANT_HEADER] = tenant
    if team:
        all_headers[TEAM_HEADER] = team
    if headers:
        all_headers.update(headers)
    # With auth enabled the L8 pass authenticates as the bootstrap admin (D-10).
    # Prefer the system-tenant key so requests may act as any tenant via the header;
    # callers that pass their own Authorization (e.g. a viewer token) are not overridden.
    admin_key = os.environ.get("HIVEPLANE_AUTH__SYSTEM_KEY") or os.environ.get(
        "HIVEPLANE_AUTH__ADMIN_KEY"
    )
    if admin_key and not any(key.lower() == "authorization" for key in all_headers):
        all_headers["Authorization"] = f"Bearer {admin_key}"
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


def get(
    path: str,
    *,
    tenant: str | None = None,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    return request("GET", path, tenant=tenant, timeout=timeout, headers=headers)


def post(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    tenant: str | None = None,
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    return request("POST", path, payload, tenant=tenant, timeout=timeout, headers=headers)


def system_headers() -> dict[str, str]:
    """Authorization for the system-tenant bootstrap key (tenant admin, M58)."""
    key = os.environ.get("HIVEPLANE_AUTH__SYSTEM_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def register(payload: dict[str, Any], *, tenant: str | None = None) -> int:
    """Register a workload (idempotent); return the HTTP status."""
    status, _ = post("/workloads", payload, tenant=tenant)
    assert status in (200, 201, 409), status
    return status


def certify(
    workload: str, *, context: str = "staging", identity: str | None = None
) -> tuple[int, Any]:
    return post(
        "/certifications",
        {
            "workload": workload,
            "target_context": context,
            "model_identity": identity or canonical_identity(),
        },
        timeout=600.0,
    )


def submit(
    workload: str,
    *,
    context: str = "sandbox",
    task: dict[str, Any] | None = None,
    identity: str | None = None,
    tenant: str | None = None,
) -> tuple[int, Any]:
    return post(
        "/runs",
        {
            "workload": workload,
            "caller": "field-test-v02",
            "context": context,
            "task": task or dict(TASK_OK),
            "model_identity": identity or canonical_identity(),
        },
        tenant=tenant,
    )


def start(run_id: str, *, tenant: str | None = None) -> int:
    status, _ = post(f"/runs/{run_id}/start", tenant=tenant)
    return status


def wait_terminal(
    run_id: str, *, timeout: float = 120.0, tenant: str | None = None
) -> dict[str, Any]:
    """Poll a run to a terminal state, auto-approving one pause."""
    deadline = time.monotonic() + timeout
    handled = False
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, run = get(f"/runs/{run_id}", tenant=tenant)
        assert status == 200, run
        if run.get("state") in TERMINAL:
            return run
        if run.get("state") == "paused" and not handled:
            approve_and_resume(run_id, tenant=tenant)
            handled = True
        time.sleep(1)
    raise AssertionError(f"run {run_id} never reached a terminal state: {run}")


def approve_and_resume(run_id: str, *, tenant: str | None = None) -> None:
    status, approvals = get("/approvals", tenant=tenant)
    assert status == 200, approvals
    pending = [
        approval
        for approval in approvals
        if approval.get("run_id") == run_id and approval.get("status") == "pending"
    ]
    assert pending, f"paused run {run_id} has no pending approval: {approvals}"
    approval_id = pending[0]["approval_id"]
    approved = post(
        f"/approvals/{approval_id}/approve",
        {"operator": "field-test-v02", "reason": "docker v02 auto-approval"},
        tenant=tenant,
    )
    assert approved[0] == 200, approved
    resumed = post(f"/runs/{run_id}/resume", tenant=tenant)
    assert resumed[0] in (200, 409), resumed


def manifest(name: str) -> dict[str, Any]:
    """Load a workload manifest (examples/ or field_test/) as an API payload."""
    from hiveplane.core.manifest import load_manifest

    for base in (FIELD_WORKLOADS_DIR, WORKLOADS_DIR):
        path = base / f"{name}.yaml"
        if path.is_file():
            return load_manifest(path).model_dump(by_alias=True, mode="json")
    raise FileNotFoundError(f"workload manifest not found for {name!r}")


def ensure_admissible(workload: str) -> None:
    """Register, reinstate any active quarantine, and certify a workload.

    The v0.2.0 scenarios deliberately exercise quarantine/defense, so a shared
    workload can be left quarantined by an earlier test. This makes each module
    start from a known-admissible state (operator-equivalent reinstate + re-cert).
    """
    register(manifest(workload))
    status, quarantines = get("/quarantines")
    if status == 200 and isinstance(quarantines, list):
        for record in quarantines:
            if (
                isinstance(record, dict)
                and record.get("workload") == workload
                and record.get("status") not in ("reinstated", None)
            ):
                certify(workload, context="production")
                post(
                    f"/quarantines/{record['quarantine_id']}/reinstate",
                    {"operator": "field-test-v02"},
                )
    certify(workload, context="staging")
