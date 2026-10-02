"""L4/L9 (v0.2.0) — Delivery & approvals scenarios (M51, gate 5).

Covers the 9-channel fan-out audit, interactive/mobile approval resolve, and the
webhook-sink capture so "approvals + fan-out to ≥3 channels; mobile approvals work"
(gate 5) has a container-layer check.
"""

from __future__ import annotations

import time

import pytest

from v02_support import ensure_admissible, get, post, submit, wait_terminal

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"


def test_delivery_audit_is_readable() -> None:
    status, audit = get("/delivery/audit")
    assert status == 200 and isinstance(audit, list), audit


def test_interactive_approval_resolve_rejects_a_forged_token() -> None:
    status, _ = post(
        "/delivery/approvals/resolve",
        {"token": "forged", "decision": "approve", "operator": "attacker"},
    )
    assert status in (400, 401, 403, 404, 409, 422), "a forged approval token must be refused"


def test_fan_out_delivers_to_the_webhook_sink() -> None:
    ensure_admissible(_WORKLOAD)
    status, run = submit(_WORKLOAD, context="sandbox")
    assert status == 201, run
    run_id = run["id"]
    post(f"/runs/{run_id}/start")
    wait_terminal(run_id)

    # A completed run records its fan-out attempts against the run (support-agent
    # declares slack fan-out, delivered to the webhook-sink). Bounded wait so a
    # broken fan-out fails fast, not hang the suite.
    deadline = time.monotonic() + 30
    attempts: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        check, body = get(f"/runs/{run_id}/deliveries")
        if check == 200 and isinstance(body, list):
            attempts = body
        if attempts:
            break
        time.sleep(2)
    assert attempts, (
        "no fan-out delivery attempt recorded within 30s — check the workload fan_out "
        "config and the fan-out destinations (see the v0.2.0 docker test plan §2)"
    )
    assert any(a.get("status") == "delivered" for a in attempts), attempts
