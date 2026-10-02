"""L11 (v0.2.0) — concurrency load test: ≥50 concurrent runs (M61-07, #482, gates 19, 33).

Submits and starts ≥50 sandbox runs concurrently, waits for terminal states, and records
throughput / latency / error rate into ``field_test/v0.2.0/results/load/``. The suite fails
on any non-terminal run or any run that failed for a control-plane reason.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from v02_support import (
    TASK_OK,
    canonical_identity,
    ensure_admissible,
    post,
    request,
)

pytestmark = pytest.mark.docker

_WORKLOAD = "support-agent"
_CONCURRENCY = 50
_RESULTS = Path(__file__).resolve().parents[2] / "field_test" / "v0.2.0" / "results" / "load"


def _submit_and_start(_: int) -> dict[str, object]:
    started = time.monotonic()
    status, run = post(
        "/runs",
        {
            "workload": _WORKLOAD,
            "caller": "load-test",
            "context": "sandbox",
            "task": dict(TASK_OK),
            "model_identity": canonical_identity(),
        },
    )
    if status != 201:
        return {"ok": False, "status": status, "run": run}
    run_id = run["id"]
    post(f"/runs/{run_id}/start")
    return {"ok": True, "run_id": run_id, "started": started}


@pytest.mark.docker
def test_sustain_50_concurrent_runs() -> None:
    ensure_admissible(_WORKLOAD)
    _RESULTS.mkdir(parents=True, exist_ok=True)

    begin = time.monotonic()
    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
        submitted = list(pool.map(_submit_and_start, range(_CONCURRENCY)))
    submit_elapsed = time.monotonic() - begin

    run_ids = [item["run_id"] for item in submitted if item.get("ok")]
    assert len(run_ids) >= _CONCURRENCY, f"only {len(run_ids)} runs admitted: {submitted}"

    deadline = time.monotonic() + 300
    terminal: dict[str, str] = {}
    while time.monotonic() < deadline and len(terminal) < len(run_ids):
        for run_id in run_ids:
            if run_id in terminal:
                continue
            status, run = request("GET", f"/runs/{run_id}")
            if status == 200 and run.get("state") in {"completed", "failed", "cancelled"}:
                terminal[str(run_id)] = str(run["state"])
        time.sleep(2)

    elapsed = time.monotonic() - begin
    failures = {rid: state for rid, state in terminal.items() if state != "completed"}
    summary = {
        "concurrency": _CONCURRENCY,
        "admitted": len(run_ids),
        "terminal": len(terminal),
        "failures": failures,
        "submit_seconds": round(submit_elapsed, 3),
        "total_seconds": round(elapsed, 3),
        "throughput_runs_per_second": round(len(run_ids) / elapsed, 3) if elapsed else 0.0,
    }
    (_RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    assert len(terminal) == len(run_ids), f"runs never terminated: {set(run_ids) - set(terminal)}"
    assert not failures, f"runs failed under load: {failures}"
