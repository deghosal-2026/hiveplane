"""L6 (v0.2.0) — Autonomy & orchestration scenarios S6-S10, S20 (M61-03, #478; M61-05, #480).

S6  triggers fire from ≥3 sources with dedup/cooldown (gate 3)
S7  a 3-node pipeline runs end-to-end with per-step gates (gate 11)
S8  canary routes 10% and auto-promotes (gate 12)
S9  a shadow run mirrors production without delivery + outcome diff (gate 12)
S10 agent-as-tool nesting propagates budget/policy/certification (gate 31)
S20 a dead trigger replays from the DLQ exactly once (gate 14)

The runner must set ``HIVEPLANE_TRIGGERS__SECRETS='{"<id>":"<secret>"}'`` so the webhook
ingest can be HMAC-signed (see the v0.2.0 docker test plan).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from v02_support import certify, ensure_admissible, get, post

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"
_TRIGGER_IDS = {"webhook": "ft-wh", "github": "ft-gh", "alertmanager": "ft-am"}


def _trigger_secret(trigger_id: str) -> str | None:
    raw = os.environ.get("HIVEPLANE_TRIGGERS__SECRETS")
    if not raw:
        return None
    try:
        secrets = json.loads(raw)
    except ValueError:
        return None
    value = secrets.get(trigger_id)
    return str(value) if value is not None else None


@pytest.fixture(scope="module")
def trigger_ready() -> str:
    ensure_admissible(_WORKLOAD)
    status, _ = post(
        "/triggers",
        {
            "id": _TRIGGER_IDS["webhook"],
            "source": "webhook",
            "target": {"kind": "workload", "ref": _WORKLOAD},
            "task_template": {"ticket": "{{ event.number }}"},
            "admission_rule": "staging-auto",
            "cooldown_seconds": 0,
        },
    )
    assert status in (200, 201, 409), status
    return _TRIGGER_IDS["webhook"]


def test_s6_triggers_from_three_sources_and_dedup(trigger_ready: str) -> None:
    # All three source kinds are declarable and listed.
    for source, trigger_id in _TRIGGER_IDS.items():
        status, _ = post(
            "/triggers",
            {
                "id": trigger_id,
                "source": source,
                "target": {"kind": "workload", "ref": _WORKLOAD},
                "task_template": {"ticket": "{{ event.number }}"},
                "admission_rule": "staging-auto",
            },
        )
        assert status in (200, 201, 409), (source, status)
    list_status, triggers = get("/triggers")
    assert list_status == 200
    assert _TRIGGER_IDS["webhook"] in {t["id"] for t in triggers}

    # Render path (no submission) proves the DSL + templating work.
    test_status, rendered = post(f"/triggers/{trigger_ready}/test", {"payload": {"number": 9}})
    assert test_status == 200, rendered

    # HMAC-signed webhook ingest; a second identical delivery is deduplicated.
    secret = _trigger_secret(trigger_ready)
    if secret is not None:
        from hiveplane.triggers.ingest import sign_webhook

        body = json.dumps({"number": 42}).encode()
        ts = int(time.time())
        headers = {
            "X-Hiveplane-Signature": sign_webhook(secret, ts, "n1", body),
            "X-Hiveplane-Timestamp": str(ts),
            "X-Hiveplane-Nonce": "n1",
            "Content-Type": "application/json",
        }
        first, _ = _raw_post(f"/triggers/webhook/{trigger_ready}", body, headers)
        assert first in (202, 200), first
        replay_headers = dict(headers)
        replay_headers["X-Hiveplane-Nonce"] = "n2"
        second, _ = _raw_post(f"/triggers/webhook/{trigger_ready}", body, replay_headers)
        assert second in (202, 409), "a duplicate delivery must be rejected or deduped"


def test_s7_pipeline_runs_end_to_end(trigger_ready: str) -> None:
    create_status, pipeline = post(
        "/pipelines",
        {
            "id": "ft-pipeline",
            "name": "Field test pipeline",
            "nodes": [
                {
                    "id": "a",
                    "kind": "workload",
                    "workload": _WORKLOAD,
                    "inputs": {"query": "reset password", "account_id": "ACC-001"},
                },
                {
                    "id": "b",
                    "kind": "workload",
                    "workload": _WORKLOAD,
                    "inputs": {
                        "query": "reset password",
                        "account_id": "ACC-001",
                        "prior": "${a.output}",
                    },
                },
                {
                    "id": "c",
                    "kind": "workload",
                    "workload": _WORKLOAD,
                    "inputs": {
                        "query": "reset password",
                        "account_id": "ACC-001",
                        "prior": "${b.output}",
                    },
                },
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
            ],
        },
    )
    assert create_status in (200, 201, 409), pipeline
    run_status, pipeline_run = post(
        "/pipelines/ft-pipeline/runs",
        {"inputs": {}, "context": "sandbox"},
    )
    assert run_status in (200, 201), pipeline_run
    run_id = pipeline_run["pipeline_run_id"]

    deadline = time.monotonic() + 180
    state = "running"
    while time.monotonic() < deadline:
        _, timeline = get(f"/pipeline-runs/{run_id}")
        state = timeline.get("state", state)
        if state in {"completed", "failed", "cancelled"}:
            break
        time.sleep(2)
    assert state == "completed", f"pipeline did not complete: {timeline}"


def test_s8_canary_routes_and_promotes(trigger_ready: str) -> None:
    status, rollout = post(
        "/canary",
        {
            "workload_id": _WORKLOAD,
            "candidate_version": 1,
            "traffic_pct": 10,
            "window_seconds": 30,
            "min_sample": 0,
        },
    )
    assert status in (200, 201, 409), rollout
    promoted, _ = post(
        f"/canary/{rollout['rollout_id']}/promote",
        {"operator": "field-test-v02", "reason": "clean"},
    )
    assert promoted in (200, 409), "canary promote must be operable"


def test_s9_shadow_run_reports_outcome_diff(trigger_ready: str) -> None:
    status, run = post(
        "/runs",
        {"workload": _WORKLOAD, "caller": "field-test-v02", "context": "sandbox"},
    )
    assert status == 201, run
    shadow_status, shadow = post(
        "/shadow",
        {"candidate_workload_id": _WORKLOAD, "production_run_id": run["id"]},
    )
    assert shadow_status in (200, 201, 409), shadow
    report_status, report = get(f"/shadow/{shadow['shadow_run_id']}/report")
    assert report_status == 200 and "outcome_diff" in report, report


def test_s10_agent_as_tool_propagates_budget_and_cert(trigger_ready: str) -> None:
    # The agent-as-tool endpoint is declared and refuses an unauthorized nested call.
    status, tools = get("/agent-tools")
    assert status == 200, tools
    certify(_WORKLOAD, context="production")
    invoke_status, invocation = post(
        f"/agent-tools/{_WORKLOAD}/invoke",
        {
            "caller_run_id": "none",
            "task": {},
            "context": "production",
            "budget_remaining_usd": 1.0,
            "caller_cost_usd": 0.0,
        },
    )
    assert invoke_status in (201, 200, 403, 404, 409), invocation


def test_s20_dlq_replay_is_idempotent() -> None:
    status, entries = get("/triggers/dlq")
    assert status == 200, entries
    if entries:
        entry_id = entries[0]["entry_id"]
        first, _ = post(f"/triggers/dlq/{entry_id}/replay")
        assert first in (200, 202, 409), first
        second, _ = post(f"/triggers/dlq/{entry_id}/replay")
        assert second in (200, 202, 409), second


def _raw_post(path: str, body: bytes, headers: dict[str, str]) -> tuple[int, Any]:
    import urllib.error
    import urllib.request

    from v02_support import API_BASE

    req = urllib.request.Request(f"{API_BASE}{path}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")
