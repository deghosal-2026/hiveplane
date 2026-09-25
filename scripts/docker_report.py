#!/usr/bin/env python3
"""Render the detailed docker test report from a JUnit XML run (M23, #93/#96).

Reads ``junit.xml`` and ``environment.json`` from a run directory, groups the
results by test layer (L0-L7), and writes a detailed Markdown report to
``docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md`` (and a per-run copy).

Exits non-zero when any test failed, errored, or — under the zero-skip policy —
skipped, so CI can gate on it.

Usage:
    scripts/docker_report.py --junit <run>/junit.xml --environment <run>/environment.json \
        --log-dir <run> --output docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Test module stem -> (layer id, layer name), in display order.
LAYERS: tuple[tuple[str, str], ...] = (
    ("L0", "Image build"),
    ("L1", "Stack health"),
    ("L2", "API contract"),
    ("L3", "UI"),
    ("L4", "Control loop"),
    ("L5", "Governance"),
    ("L6", "Durability"),
    ("L7", "LLM matrix"),
)

_MODULE_LAYER: dict[str, str] = {
    "test_container_fixtures": "L0",
    "test_stack_health": "L1",
    "test_api_contract": "L2",
    "test_ui": "L3",
    "test_control_loop": "L4",
    "test_governance": "L5",
    "test_durability": "L6",
    "test_llm_matrix": "L7",
}

ISSUES_AND_LEARNINGS: tuple[str, ...] = (
    (
        "LLM selection matters: the docker stack now uses "
        "`Qwen3-4B-Instruct-2507-4bit` via canonical identity "
        "`omlx/qwen3-4b-instruct-2507/4bit` because the non-Instruct Qwen3.5/8B "
        "models emitted reasoning prose instead of the strict JSON contract and "
        "were materially slower."
    ),
    (
        "Small local models need an explicit rubric: the repo-agent prompt was "
        "hardened so tests-only changes stay low risk, auth/token changes stay "
        "high risk, destructive migrations stay high risk, and prompt-injection "
        "text in a PR body is classified as high risk."
    ),
    (
        "Concurrent `run_events` appends were not race-safe under Postgres READ "
        "COMMITTED. The store now locks the parent run row (`FOR UPDATE`) before "
        "assigning the next sequence and persists the assigned sequence in the "
        "event payload."
    ),
    (
        "Startup recovery must tolerate orphaned runs. Recovery now skips "
        "non-terminal runs whose workload has been deleted instead of crashing "
        "the API during lifespan startup."
    ),
    (
        "The docker runner now resets volumes at start, records a PID, "
        "heartbeat, current phase, and abort marker so interrupted runs are "
        "diagnosable instead of silently leaving partial evidence."
    ),
    (
        "The control-loop docker test depends on certification lifecycle "
        "semantics: staging must produce `provisional` before production can "
        "produce `certified`, and the field-test profile lowers "
        "`min_production_runs_survived` to `0` so the loop can be demonstrated "
        "in one stack run without weakening the benchmark thresholds themselves."
    ),
    (
        "The UI seeded run must use the same canonical model identity as the "
        "certification path; hardcoding `gpt-4o` correctly triggered the "
        "model-swap gate once repo-agent was certified against the local model."
    ),
)


@dataclass
class TestResult:
    """One JUnit test case."""

    layer: str
    name: str
    status: str
    duration_s: float
    message: str = ""


@dataclass
class LayerSummary:
    """Aggregated counts for one layer."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    results: list[TestResult] = field(default_factory=list)


def _status(case: ET.Element) -> tuple[str, str]:
    for kind in ("failure", "error", "skipped"):
        child = case.find(kind)
        if child is not None:
            message = child.get("message") or (child.text or "")
            return kind, " ".join(message.split())[:600]
    return "passed", ""


def _module_of(case: ET.Element) -> str:
    file = case.get("file")
    if file:
        return Path(file).stem
    classname = case.get("classname", "")
    return classname.split(".")[-1] if classname else "unknown"


def parse_junit(path: Path) -> list[TestResult]:
    """Parse a JUnit XML file into per-test results."""
    if not path.is_file():
        return []
    tree = ET.parse(path)
    results: list[TestResult] = []
    for case in tree.iter("testcase"):
        module = _module_of(case)
        layer = _MODULE_LAYER.get(module, "L?")
        status, message = _status(case)
        try:
            duration = float(case.get("time", "0") or "0")
        except ValueError:
            duration = 0.0
        results.append(
            TestResult(
                layer=layer,
                name=f"{module}::{case.get('name', '')}",
                status=status,
                duration_s=duration,
                message=message,
            )
        )
    return results


def summarize(results: list[TestResult]) -> dict[str, LayerSummary]:
    """Group results by layer."""
    field_by_status = {
        "passed": "passed",
        "failure": "failed",
        "error": "errored",
        "skipped": "skipped",
    }
    summaries = {layer: LayerSummary() for layer, _ in LAYERS}
    for result in results:
        summary = summaries.setdefault(result.layer, LayerSummary())
        summary.total += 1
        summary.duration_s += result.duration_s
        summary.results.append(result)
        count_field = field_by_status.get(result.status)
        if count_field is None:
            continue
        setattr(summary, count_field, getattr(summary, count_field) + 1)
    return summaries


def render(
    results: list[TestResult],
    environment: dict[str, object],
    log_dir: Path,
) -> str:
    """Render the full Markdown report."""
    summaries = summarize(results)
    failed = sum(s.failed for s in summaries.values())
    errored = sum(s.errored for s in summaries.values())
    skipped = sum(s.skipped for s in summaries.values())
    passed = sum(s.passed for s in summaries.values())
    total = sum(s.total for s in summaries.values())

    policy_violation = skipped > 0
    overall = "PASS" if (failed == 0 and errored == 0 and not policy_violation) else "FAIL"
    if total == 0:
        overall = "NOT RUN"

    lines: list[str] = [
        "# HivePlane v0.1.0 — Docker Test Report",
        "",
        f"> Generated {datetime.now(UTC).isoformat()} by `scripts/docker-test.sh`.",
        f"> **Overall: {overall}** — {passed} passed, {failed} failed, "
        f"{errored} errored, {skipped} skipped (of {total}).",
    ]
    if policy_violation:
        lines.append(
            "> **Zero-skip policy violated:** the docker suite must not skip; "
            "every layer is required."
        )
    lines += ["", "## Issues / Learnings", ""]
    for item in ISSUES_AND_LEARNINGS:
        lines.append(f"- {item}")
    lines += ["", "## Environment", "", "| Key | Value |", "|-----|-------|"]
    for key in sorted(environment):
        lines.append(f"| `{key}` | `{environment[key]}` |")
    if not environment:
        lines.append("| _none recorded_ | |")

    lines += [
        "",
        "## Layer summary",
        "",
        "| Layer | Name | Tests | Passed | Failed | Errored | Skipped | Duration (s) |",
        "|-------|------|-------|--------|--------|---------|---------|--------------|",
    ]
    for layer, name in LAYERS:
        summary = summaries.get(layer, LayerSummary())
        lines.append(
            f"| {layer} | {name} | {summary.total} | {summary.passed} | "
            f"{summary.failed} | {summary.errored} | {summary.skipped} | "
            f"{summary.duration_s:.2f} |"
        )

    lines += ["", "## Layer detail", ""]
    for layer, name in LAYERS:
        summary = summaries.get(layer, LayerSummary())
        lines += [f"### {layer} — {name}", ""]
        if not summary.results:
            lines += ["_No tests recorded for this layer._", ""]
            continue
        lines += ["| Test | Status | Duration (s) |", "|------|--------|--------------|"]
        for result in summary.results:
            lines.append(
                f"| `{result.name}` | {result.status} | {result.duration_s:.2f} |"
            )
        lines.append("")
        for result in summary.results:
            if result.message:
                lines += [
                    f"<details><summary>{result.name} — {result.status}</summary>",
                    "",
                    "```",
                    result.message,
                    "```",
                    "",
                    "</details>",
                    "",
                ]

    lines += ["", "## Logs", "", "| Artifact | Bytes |", "|----------|-------|"]
    if log_dir.is_dir():
        for artifact in sorted(log_dir.iterdir()):
            if artifact.is_file():
                lines.append(f"| `{artifact.name}` | {artifact.stat().st_size} |")
    else:
        lines.append("| _log directory missing_ | |")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    environment: dict[str, object] = {}
    if args.environment and args.environment.is_file():
        environment = json.loads(args.environment.read_text(encoding="utf-8"))

    results = parse_junit(args.junit)
    report = render(results, environment, args.log_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    (args.log_dir / "report.md").write_text(report, encoding="utf-8")

    failed = sum(1 for r in results if r.status in ("failure", "error"))
    skipped = sum(1 for r in results if r.status == "skipped")
    print(f"wrote {args.output} ({len(results)} tests, {failed} failed, {skipped} skipped)")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
