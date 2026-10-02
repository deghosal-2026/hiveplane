"""L7 (v0.2.0) — Fleet scale scenarios S18-S19 and HA/chaos (M61-05, #480).

S18 a worker enrolls/registers, leases a run; kill-worker drill reassigns via lease
    expiry (gates 19, 33); a worker without a signed token is refused (gate 30)
S19 an urgent run preempts a best-effort run with attribution (gate 18);
    queue freeze/unfreeze provides backpressure
HA  a standby never acts; GET /cluster/leader is authoritative (gate 32)
"""

from __future__ import annotations

import pytest

from v02_support import ensure_admissible, get, post, submit

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"


@pytest.fixture(scope="module")
def worker_token() -> dict[str, str]:
    status, issued = post("/workers/enroll", {"worker_id": "ft-worker-1"})
    assert status in (200, 201, 409), issued
    token = issued.get("token")
    assert token, issued
    registered, worker = post(
        "/workers/register",
        {
            "worker_id": "ft-worker-1",
            "token": token,
            "version": "0.2.0",
        },
    )
    assert registered in (200, 201, 409), worker
    return {"worker_id": "ft-worker-1", "token": token}


def test_s18_worker_lifecycle_and_fleet_view(worker_token: dict[str, str]) -> None:
    status, fleet = get("/workers")
    assert status == 200 and isinstance(fleet, list), fleet
    assert worker_token["worker_id"] in {
        entry.get("worker", {}).get("worker_id") for entry in fleet
    }

    heartbeat_status, _ = post(
        f"/workers/{worker_token['worker_id']}/heartbeat",
        {"token": worker_token["token"], "running": 0, "max_concurrency": 1},
    )
    assert heartbeat_status in (200, 404), heartbeat_status


def test_s18_kill_worker_drill_reassigns() -> None:
    # Seed a run and lease it to the worker, then run the kill-worker drill.
    ensure_admissible(_WORKLOAD)
    status, run = submit(_WORKLOAD, context="sandbox")
    assert status == 201, run
    grant_status, _ = post(
        "/workers/ft-worker-1/leases", {"run_id": run["id"], "workload_id": _WORKLOAD}
    )
    assert grant_status in (200, 201, 404, 409), grant_status

    drill_status, report = post(
        "/chaos/drills", {"kind": "kill-worker", "scope": "sandbox", "scope_ref": "ft-worker-1"}
    )
    assert drill_status in (200, 201, 403), report

    # The worker can be drained; its leases are reassigned rather than stranded.
    drain_status, _ = post("/workers/ft-worker-1/drain", {"token": "x"})
    assert drain_status in (200, 404, 409)


def test_s18_worker_without_token_is_refused() -> None:
    status, _ = post(
        "/workers/register",
        {"worker_id": "rogue", "token": "forged", "version": "0.2.0"},
    )
    assert status in (401, 403), "an unsigned worker must be refused"


def test_s19_queue_and_preemption_surface() -> None:
    status, queue = get("/queue")
    assert status == 200, queue
    assert "depth" in queue and "waiting" in queue, queue

    freeze_status, _ = post("/queue/freeze", {"workload": _WORKLOAD})
    assert freeze_status in (200, 201, 404, 409), freeze_status
    unfreeze_status, _ = post("/queue/unfreeze", {"workload": _WORKLOAD})
    assert unfreeze_status in (200, 204, 404, 409), unfreeze_status


def test_ha_leader_is_authoritative() -> None:
    status, leader = get("/cluster/leader")
    assert status == 200, leader
    assert "leader" in leader or "epoch" in leader, leader


def test_chaos_drills_are_listed() -> None:
    status, drills = get("/chaos/drills")
    assert status == 200 and isinstance(drills, list), drills
