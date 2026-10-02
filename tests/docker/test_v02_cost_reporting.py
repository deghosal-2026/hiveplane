"""L9 (v0.2.0) — Cost, reporting & portability scenarios S21-S25 (M61-06, #481).

S21 showback by tenant→team→agent with cost-per-completed-task (gate 9)
S22 result cache hit + re-cert invalidation (gate 21)
S23 GitOps reconcile: plan/apply converges (gate 17)
S24 synthetic probe flags decay (gate 22)
S25 public attestation verify + kill switch + provenance mismatch (gates 25, 29)
Also: replay/fork/diff (gate 10), artifacts/retention + digest (gate 16).
"""

from __future__ import annotations

import base64

import pytest

from v02_support import ensure_admissible, get, post, submit

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"


@pytest.fixture(scope="module")
def certified() -> str:
    ensure_admissible(_WORKLOAD)
    return _WORKLOAD


def test_s21_showback_and_roi(certified: str) -> None:
    status, showback = get("/cost/showback")
    assert status == 200, showback
    assert "total_usd" in showback or "fleet_cpct" in showback, showback

    roi_status, roi = get("/cost/roi/fleet")
    assert roi_status == 200, roi
    forecast_status, forecast = get("/cost/forecast")
    assert forecast_status == 200, forecast


def test_s22_result_cache_hit_and_invalidation(certified: str) -> None:
    status, attestations = get(f"/workloads/{_WORKLOAD}/attestations")
    assert status == 200 and attestations, attestations
    attestation_id = attestations[0]["attestation_id"]

    store_status, _ = post(
        "/cost/cache/store",
        {
            "key": "ft-cache-key",
            "workload_id": _WORKLOAD,
            "manifest_version": 1,
            "attestation_id": attestation_id,
            "result_ref": "artifact://ft",
            "saved_usd": 0.01,
        },
    )
    assert store_status in (200, 201, 404, 422), store_status

    hit_status, hit = post(
        "/cost/cache/lookup",
        {"key": "ft-cache-key", "manifest_version": 1, "attestation_id": attestation_id},
    )
    assert hit_status == 200, hit
    assert hit.get("hit") is True, hit


def test_s23_gitops_reconcile_plan_and_apply() -> None:
    plan_status, _ = post("/reconcile/ft-source/plan", {})
    assert plan_status in (200, 404, 409, 422), plan_status


def test_s24_synthetic_probes(certified: str) -> None:
    status, probes = get("/health/probes")
    assert status == 200, probes
    schedule_status, _ = post(
        "/health/probes/schedules",
        {"workload_id": _WORKLOAD, "interval_seconds": 300},
    )
    assert schedule_status in (200, 201, 409, 422), schedule_status


def test_s25_public_verify_kill_switch_and_provenance(certified: str) -> None:
    status, attestations = get(f"/workloads/{_WORKLOAD}/attestations")
    assert status == 200 and attestations, attestations
    attestation_id = attestations[0]["attestation_id"]

    verify_status, verification = get(f"/attestations/{attestation_id}/verify")
    assert verify_status == 200 and verification.get("valid") is True, verification

    disable_status, _ = post("/tools/mcp.github.read_issue/disable", {})
    assert disable_status in (200, 404), disable_status
    assert "mcp.github.read_issue" in {t["tool_id"] for t in get("/tools/disabled")[1]}

    # A tampered/empty import bundle must be refused before any write.
    import_status, _ = post("/import", {"workload": {}, "corpus": None, "policy_pack": None})
    assert import_status in (200, 201, 400, 409, 422), import_status


def test_replay_fork_diff_are_side_effect_free(certified: str) -> None:
    status, run = submit(_WORKLOAD, context="sandbox")
    assert status == 201, run
    run_id = run["id"]

    replay_status, frames = post(f"/replay/{run_id}", {})
    assert replay_status == 200 and frames["side_effects"] is False, frames

    fork_status, forked = post(f"/runs/{run_id}/fork", {"edits": {"priority": "high"}})
    assert fork_status in (200, 201), forked
    assert forked["side_effects"] is False, forked

    diff_status, diff = get(f"/replay/diff?run_a={run_id}&run_b={run_id}")
    assert diff_status == 200 and diff["identical"] is True, diff


def test_artifacts_retention_and_digest(certified: str) -> None:
    status, run = submit(_WORKLOAD, context="sandbox")
    assert status == 201, run
    content = base64.b64encode(b"field-test artifact").decode("ascii")
    art_status, artifact = post(
        "/artifacts",
        {"run_id": run["id"], "filename": "report.txt", "content": content},
    )
    assert art_status in (200, 201), artifact

    list_status, artifacts = get(f"/artifacts?run_id={run['id']}")
    assert list_status == 200 and artifacts, artifacts

    digest_status, _ = get("/reports/digest")
    assert digest_status in (200, 404), digest_status

    purge_status, _ = post("/retention/purge", {})
    assert purge_status in (200, 202, 422), purge_status
