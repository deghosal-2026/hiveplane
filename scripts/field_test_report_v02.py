#!/usr/bin/env python3
"""Render docs/field-test/v0.2.0/FIELD_TEST_REPORT.md from field-test results (M61-09).

Reads ``field_test/v0.2.0/results/summary.json`` — a list of scenario records produced by
the v0.2.0 docker scenario suite and the field-test runner — and renders a narrative
report with the S1-S25 pass/fail table, the 34-gate mapping, and evidence links.

Usage:
    scripts/field_test_report_v02.py \
        --summary field_test/v0.2.0/results/summary.json \
        --output docs/field-test/v0.2.0/FIELD_TEST_REPORT.md
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

#: Scenario id -> (title, [release gates], evidence dir name).
SCENARIOS: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("S1", "Certification + signed attestation; uncertified refused", (1, 25)),
    ("S2", "Promotion gate blocks a regression with a replayable diff", (2,)),
    ("S3", "Regression diff classifies pass->fail", (2,)),
    ("S4", "Drift -> auto-quarantine + notify", (1,)),
    ("S5", "Reinstatement after re-cert", (1,)),
    ("S6", "Triggers from >=3 sources with dedup/cooldown", (3,)),
    ("S7", "3-node pipeline end-to-end with per-step gates", (11,)),
    ("S8", "Canary 10% -> auto-promote", (12,)),
    ("S9", "Shadow run (no delivery) + outcome diff", (12,)),
    ("S10", "Agent-as-tool propagates budget/policy/cert", (31,)),
    ("S11", "Injection blocked; repeated attempts quarantine", (4,)),
    ("S12", "Context budget exceeded -> clean pause + accounting", (20,)),
    ("S13", "Spend-velocity breach -> pause", (20,)),
    ("S14", "Circuit breaker trips and recovers", (8, 14)),
    ("S15", "Egress denied and audited", (8,)),
    ("S16", "Secret never leaks; cross-tenant ref rejected", (13,)),
    ("S17", "RBAC viewer denial + per-tenant isolation + 429", (6, 34)),
    ("S18", "Worker kill -> lease reassign", (19, 33)),
    ("S19", "Urgent preempts best-effort, attributed", (18,)),
    ("S20", "Dead trigger replays from DLQ once", (14,)),
    ("S21", "Showback + cost-per-completed-task", (9,)),
    ("S22", "Cache hit + savings; re-cert invalidates", (21,)),
    ("S23", "GitOps: delete -> deregister; threshold change -> re-cert", (17,)),
    ("S24", "Synthetic probe flags decay before drift trips", (22,)),
    ("S25", "Public verify + kill switch + provenance + worker-token refusal", (25, 29, 30)),
    ("S26", "Run fan-out delivery is auditable and reaches the sink (regression)", (5,)),
    ("S27", "Trigger-driven run executes to completion (regression)", (3,)),
    ("S28", "Defense ordering: denied tool vs allowed tool + injection (regression)", (4, 8)),
    ("S29", "Run fan-out audit vs M51 notification audit are distinct (regression)", (5, 15)),
    ("S30", "Run fan-out failure path records an on_failed delivery (regression)", (5,)),
    ("S31", "Pipeline retry starts exactly one child per attempt (regression)", (11,)),
    ("H1", "The running API image matches the source revision", ()),
    ("H2", "Execution-model contract: queued runs do not auto-execute", ()),
    ("H3", "Concurrent run-event appends get unique sequences", ()),
    ("H4", "Startup recovery tolerates an orphaned run", ()),
    ("H5", "Quarantine -> reinstate full cycle while quarantined", (1,)),
)

GATES: tuple[str, ...] = (
    "Drifting agent auto-quarantined, notified, reinstated after re-cert",
    "Promotion gate blocks a regression with a replayable diff",
    "Triggers fire from >=3 sources with dedup/cooldown",
    "Seeded injection blocked; repeated attempts quarantine",
    "Slack approvals + fan-out to >=3 channels; mobile approvals",
    "Per-tenant budget/policy/key isolation; viewer cannot approve",
    "Helm chart deploys the full stack to a k3d cluster",
    "Health dashboard, burn-through throttle, breaker trips/recovers",
    "Showback by tenant -> team -> agent with cost-per-completed-task",
    "Frame-by-frame replay + run diff; forked run re-runs edited state",
    "Pipeline runs a multi-agent DAG end-to-end with per-step gates",
    "Canary routes 10% and auto-promotes on clean results",
    "A secret never appears in logs/traces/agent context",
    "Dead trigger replays from DLQ; breaker trips/recovers",
    "SDK + API v2 round-trip; a plugin hook fires",
    "Weekly digest auto-generates; artifact stored, linked, retained",
    "Git deletes an agent -> deregister; threshold change -> re-cert",
    "Urgent run preempts best-effort with attribution",
    "Worker on a second host; kill -> lease expiry reassigns",
    "Context budget exceeded -> clean pause with accounting",
    "Cache hit reuses result + shows savings; re-cert invalidates",
    "Synthetic probe flags decay before drift threshold",
    "`ask` answers 5 live-state questions under budget + cert",
    "Incident mode halts fleet in <5s and broadcasts",
    "Attestation verifies publicly; kill switch disables a tool",
    "Signed image + SBOM; retention purge deletes tenant data",
    "Operator-flagged failed run becomes a corpus case",
    "Sampled production runs get judge scores; quality dip alerts",
    "Modified bundle fails admission on provenance mismatch",
    "Worker without a signed token is refused",
    "Agent-as-tool calls propagate budget/policy/certification",
    "Second controller replica does not double-reconcile",
    "Chaos drills recover/halt (kill worker; revoke cert)",
    "Per-workload service endpoint serves a run through all gates; 429s",
)


def load_summary(path: Path) -> dict[str, dict[str, object]]:
    """Return scenario id -> record from the summary file (empty when absent)."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else payload.get("scenarios", [])
    result: dict[str, dict[str, object]] = {}
    for record in records:
        scenario = str(record.get("scenario") or record.get("id") or "")
        if scenario:
            result[scenario] = record
    return result


def _status(record: dict[str, object] | None) -> str:
    """Map a scenario record to a display status (pass/fail/blocked/not run)."""
    if record is None:
        return "not run"
    state = str(record.get("status") or ("pass" if record.get("passed") else "fail")).lower()
    return {"pass": "PASS", "fail": "FAIL", "blocked": "BLOCKED"}.get(state, state.upper())


def _evidence_files(results_dir: Path, scenario: str) -> list[str]:
    """Return the artifact names under the scenario's evidence directory."""
    if not results_dir.is_dir():
        return []
    for directory in sorted(results_dir.glob(f"{scenario}-*")):
        if directory.is_dir():
            return sorted(path.name for path in directory.iterdir() if path.is_file())
    return []


def render(summary: dict[str, dict[str, object]], *, results_dir: Path | None = None) -> str:
    """Render the complete v0.2.0 field-test report from the summary + evidence."""
    results_dir = results_dir or Path("field_test/v0.2.0/results")

    def status(scenario: str) -> str:
        return _status(summary.get(scenario))

    passed = sum(1 for scenario, *_ in SCENARIOS if status(scenario) == "PASS")
    blocked = sum(1 for scenario, *_ in SCENARIOS if status(scenario) == "BLOCKED")
    failed = sum(1 for scenario, *_ in SCENARIOS if status(scenario) == "FAIL")
    lines: list[str] = [
        "# HivePlane v0.2.0 — Field Test Report (generated)",
        "",
        f"> Generated {datetime.now(UTC).isoformat()} from "
        "`field_test/v0.2.0/results/summary.json` and the committed evidence.",
        f"> **Scenarios: {passed}/{len(SCENARIOS)} passed"
        + (f", {blocked} blocked" if blocked else "")
        + (f", {failed} failed" if failed else "")
        + ".**",
        ">",
        "> This file is **generated**; the curated `FIELD_TEST_REPORT.md` is hand-edited from it.",
        "",
        "## Scenario results",
        "",
        "| # | Scenario | Gates | Result | Evidence |",
        "|---|----------|-------|--------|----------|",
    ]
    for scenario, title, gates in SCENARIOS:
        gate_list = ", ".join(str(gate) for gate in gates)
        lines.append(
            f"| {scenario} | {title} | {gate_list} | {status(scenario)} | "
            f"`field_test/v0.2.0/results/{scenario}-*/` |"
        )

    lines += [
        "",
        "## Per-scenario detail",
        "",
        "| Scenario | Result | Detail |",
        "|----------|--------|--------|",
    ]
    for scenario, _title, _gates in SCENARIOS:
        record = summary.get(scenario) or {}
        detail = str(record.get("detail") or "—").replace("|", "\\|")
        lines.append(f"| {scenario} | {status(scenario)} | {detail} |")

    lines += [
        "",
        "## Release-gate coverage (34 gates)",
        "",
        "Every gate must name an asserting scenario; a gate with none is **not yet demonstrated** "
        "(never `load`/`review`).",
        "",
        "| Gate | Demonstrated by |",
        "|------|-----------------|",
    ]
    for index, description in enumerate(GATES, start=1):
        covered = [scenario for scenario, _title, gates in SCENARIOS if index in gates]
        asserting = [scenario for scenario in covered if status(scenario) == "PASS"]
        if asserting:
            cell = ", ".join(asserting)
        elif covered:
            cell = f"**not yet demonstrated** (attempted: {', '.join(covered)})"
        else:
            cell = "**not yet demonstrated**"
        lines.append(f"| {index}. {description} | {cell} |")

    lines += ["", "## Evidence index", ""]
    for scenario, _title, _gates in SCENARIOS:
        files = _evidence_files(results_dir, scenario)
        listing = ", ".join(f"`{name}`" for name in files) or "_no artifacts_"
        lines.append(f"- **{scenario}** — {listing}")

    lines += [
        "",
        "## See also",
        "",
        "- [docker-test-plan.md](docker-test-plan.md) - container layer (L0-L13)",
        "- [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md) - container-layer results",
        "- `field_test/v0.2.0/results/` - raw per-scenario evidence (incl. `load/summary.json`)",
        "- Curated narrative: [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("field_test/v0.2.0/results/summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("field_test/v0.2.0/results/report.generated.md"),
        help="Generated report path (never the curated FIELD_TEST_REPORT.md).",
    )
    args = parser.parse_args()

    summary = load_summary(args.summary)
    report = render(summary, results_dir=args.summary.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"wrote {args.output} ({len(summary)} scenario records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
