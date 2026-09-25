#!/usr/bin/env python3
"""Render docs/field-test/v0.1.0/FIELD_TEST_REPORT.md from field-test results.

Reads ``field_test/v0.1.0/results/summary.json`` (written by
``scripts/field_test_runner.py``) and produces the field-test report with the
per-scenario results, evidence links, and the acceptance-criteria mapping.

Usage:
    scripts/field_test_report.py --results-dir <dir> --output <md>
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Scenario -> acceptance criteria it evidences.
SCENARIO_CRITERIA: dict[str, tuple[str, ...]] = {
    "S1": ("A1", "A2", "A5", "A7"),
    "S2": ("A3",),
    "S3": ("A6",),
    "S4": ("A4",),
    "S5": ("A8",),
    "S6": ("A9", "A11"),
    "S7": ("A10",),
    "S8": ("A12", "A13"),
    "S9": ("A14",),
}

CRITERIA = {
    "A1": "Three real agents registered",
    "A2": "At least one agent certified for production via benchmark",
    "A3": "Uncertified agent refused production admission",
    "A4": "Seeded manifest change blocked by re-certification (regression)",
    "A5": "Attestation signed and verified on read",
    "A6": "Model-swap blocked (certified on A, running on B)",
    "A7": "Agents through full lifecycle",
    "A8": "Budget enforcement blocks an over-budget run",
    "A9": "Execution isolation caps a destructive run",
    "A10": "Tool-output shaping truncates a large payload",
    "A11": "Guarded tool call requires approval",
    "A12": "Paused run survives control-plane restart",
    "A13": "Operators can inspect and stop any run from one surface",
    "A14": "Result fan-out delivered to configured destinations",
    "A15": "Audit trail complete for every run in the field test",
    "A16": "Median time to inspect and stop a bad run",
    "A17": "Docker Compose stack starts with one command",
    "A18": "hiveplane init scaffolds a working project in < 5 minutes",
    "A19": "Certification dashboard renders fleet cert status",
    "A20": "Spend view shows cost showback by team and agent",
}

_STATUS_ICON = {"pass": "✅", "fail": "❌", "blocked": "⏸️"}


def _criteria_status(summary: dict[str, Any]) -> dict[str, str]:
    """Map each acceptance criterion to the status of its scenarios."""
    by_id = {scenario["scenario"]: scenario["status"] for scenario in summary["scenarios"]}
    result: dict[str, str] = {}
    for sid, criteria in SCENARIO_CRITERIA.items():
        status = by_id.get(sid)
        if status is None:
            continue
        for criterion in criteria:
            current = result.get(criterion)
            if current == "fail" or status == "fail":
                result[criterion] = "fail"
            elif current == "blocked" or status == "blocked":
                result[criterion] = "blocked"
            else:
                result[criterion] = "pass"
    return result


def render(summary: dict[str, Any], results_dir: Path) -> str:
    scenarios = summary.get("scenarios", [])
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    blocked = summary.get("blocked", 0)
    overall = "PASS" if failed == 0 and blocked == 0 else ("FAIL" if failed else "INCOMPLETE")

    lines: list[str] = [
        "# HivePlane v0.1.0 — Field Test Report",
        "",
        f"> Generated {datetime.now(UTC).isoformat()} by `scripts/field-test.sh`.",
        f"> **Overall: {overall}** — {passed} passed, {failed} failed, {blocked} blocked.",
        "",
        "## Environment",
        "",
        "| Item | Value |",
        "|------|-------|",
        "| HivePlane version | v0.1.0 |",
        f"| Model identity | `{summary.get('model_identity', '—')}` |",
        f"| Generated at | {summary.get('generated_at', '—')} |",
        f"| Results directory | `{results_dir}` |",
        "",
        "This is the **real-agent** field test (three Tier 1 workloads). "
        "Container/API/UI layers are",
        "covered by [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md).",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Name | Status | Detail | Evidence |",
        "|----------|------|--------|--------|----------|",
    ]
    for scenario in scenarios:
        icon = _STATUS_ICON.get(scenario["status"], scenario["status"])
        evidence = scenario.get("evidence") or "—"
        lines.append(
            f"| {scenario['scenario']} | {scenario['name']} | {icon} {scenario['status']} | "
            f"{scenario['detail']} | `{evidence}` |"
        )

    lines += [
        "",
        "## Acceptance Criteria",
        "",
        "| # | Criterion | Result |",
        "|---|-----------|--------|",
    ]
    criteria_status = _criteria_status(summary)
    for criterion, description in CRITERIA.items():
        status = criteria_status.get(criterion, "—")
        icon = _STATUS_ICON.get(status, status)
        lines.append(f"| {criterion} | {description} | {icon} {status} |")

    lines += [
        "",
        "## Learnings",
        "",
        "_To be completed._",
        "",
        "## Known Issues",
        "",
        "- Scenarios marked ⏸️ blocked need a fixture or provider that the local $0 "
        "model cannot",
        "  exercise (see each scenario's `notes.md` under `field_test/v0.1.0/results/`).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary_path = args.results_dir / "summary.json"
    if not summary_path.is_file():
        print(f"no summary at {summary_path}", flush=True)
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    report = render(summary, args.results_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    (args.results_dir / "report.md").write_text(report, encoding="utf-8")
    print(
        f"wrote {args.output} ({summary.get('passed', 0)} passed, "
        f"{summary.get('failed', 0)} failed, {summary.get('blocked', 0)} blocked)",
        flush=True,
    )
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
