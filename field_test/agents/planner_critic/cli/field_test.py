"""Field test CLI — ``plancritic field-test run`` (M9).

Drives the field test harness against real goals and real LLMs. Supports
domain batching: pass ``--goals docs/field-test/goals/database/`` to run
only that domain.

Output structure::

    <output>/
      run.log                 # stdout+stderr captured during the run
      report.json             # machine-readable summary (parseable)
      report.md               # human-readable markdown report
      summary.json            # harness summary (per-dimension stats)
      core-api/<goal>/
        trace.json            # full loop result (plan, findings, checks)
        llm-logs/<goal>_planner_llm_log.jsonl  # every prompt + raw response
        llm-logs/<goal>_critic_llm_log.jsonl
      ...
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..loop import LoopConfig

logger = logging.getLogger(__name__)


def build_field_test_parser() -> argparse.ArgumentParser:
    """Build the ``field-test`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic field-test",
        description="Field test: run real goals against a real LLM and check invariants",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="field_command", required=True)

    run_parser = sub.add_parser("run", help="Run the field test sweep")
    run_parser.add_argument("--goals", required=True, help="Goals directory or single goal file")
    run_parser.add_argument(
        "--output", required=True, help="Output directory for traces, logs, report"
    )
    run_parser.add_argument("--config", default="plancritic.toml", help="Provider TOML config path")
    run_parser.add_argument(
        "--revision-cap", type=int, default=4, help="Loop revision cap (default: 4)"
    )
    run_parser.add_argument(
        "--critique-mode",
        choices=["heuristic-only", "deterministic-first", "llm-every-revision"],
        default="deterministic-first",
        help="Critique mode (default: deterministic-first)",
    )
    run_parser.add_argument(
        "--dimensions", default=None, help="Comma-separated dimension names to run (default: all)"
    )
    return parser


def _write_report(summary: dict[str, Any], output_path: Path) -> None:
    """Write JSON report (parseable) and markdown report (human-readable)."""
    report_path = output_path / "report.json"
    report_path.write_text(json.dumps(summary, indent=2, default=str))

    lines = [
        "# Field Test Report",
        "",
        f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Config:** {summary.get('meta', {}).get('config', '?')}",
        f"**Loop:** {summary.get('meta', {}).get('loop_config', {})}",
        f"**Total executions:** {summary.get('total', 0)}",
        f"**Passed:** {summary.get('passed', 0)}",
        f"**Failed:** {summary.get('failed', 0)}",
        f"**Pass rate:** {summary.get('pass_rate', 0) * 100:.0f}%",
        "",
        "## Dimensions",
        "",
        "| Dimension | Total | Passed | Failed |",
        "|-----------|-------|--------|--------|",
    ]
    for dim, stats in summary.get("dimensions", {}).items():
        lines.append(f"| {dim} | {stats['total']} | {stats['passed']} | {stats['failed']} |")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    failures = summary.get("failures", [])
    if failures:
        lines.append("| Dimension | Goal | Error |")
        lines.append("|-----------|------|-------|")
        for f in failures:
            lines.append(
                f"| {f.get('dimension', '?')} | {f.get('goal_id', '?')} | {str(f.get('error', '?'))[:30]} |"  # noqa: E501
            )
    else:
        lines.append("No failures.")

    # Per-goal results table from dimension traces
    lines.append("")
    lines.append("## Per-Goal Results (core-api dimension)")
    lines.append("")
    lines.append("| Goal | Pass | Status | Reason | Revs | LLM Calls | Tasks | Findings |")
    lines.append("|------|------|--------|--------|------|-----------|-------|----------|")
    for goal_dir in (
        sorted((output_path / "core-api").iterdir()) if (output_path / "core-api").exists() else []
    ):
        trace_file = goal_dir / "trace.json"
        if not trace_file.exists():
            continue
        try:
            t = json.loads(trace_file.read_text())
            r = t.get("result", {})
            plan = t.get("plan") or {}
            lines.append(
                f"| {t.get('goal_id', goal_dir.name)} | "
                f"{'✅' if t.get('pass') else '❌'} | "
                f"{r.get('status', '?')} | "
                f"{r.get('reason_code', '-')} | "
                f"{r.get('revision_count', '-')} | "
                f"{r.get('llm_calls', '-')} | "
                f"{len(plan.get('tasks', []))} | "
                f"{len(t.get('findings', []))} |"
            )
        except Exception:
            lines.append(f"| {goal_dir.name} | ❌ | parse error | - | - | - | - | - |")

    md_path = output_path / "report.md"
    md_path.write_text("\n".join(lines) + "\n")
    logger.info("Report: %s + %s", report_path, md_path)


def _setup_run_logging(output_dir: Path) -> logging.FileHandler:
    """Add a file handler that captures all log output to run.log."""
    fh = logging.FileHandler(output_dir / "run.log", mode="w")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    return fh


def run_field_test(argv: list[str]) -> int:
    """Run the field test CLI."""
    parser = build_field_test_parser()
    args = parser.parse_args(argv)

    if args.field_command != "run":
        parser.print_help()
        return 1

    goals_dir = Path(args.goals)
    if not goals_dir.exists():
        logger.error("goals path not found: %s", goals_dir)
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Set up logging to capture everything to run.log
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    fh = _setup_run_logging(output_dir)
    run_start_msg = f"=== Field test run started {datetime.now(UTC).isoformat()} ==="
    logger.info(run_start_msg)
    logger.info("Goals: %s", goals_dir)
    logger.info("Output: %s", output_dir)
    logger.info("Config: %s", args.config)
    logger.info("Revision cap: %s", args.revision_cap)
    logger.info("Critique mode: %s", args.critique_mode)

    loop_config = LoopConfig(mode=args.critique_mode, revision_cap=args.revision_cap)

    dimensions = None
    if args.dimensions:
        dimensions = [d.strip() for d in args.dimensions.split(",")]

    from ..field_test_harness import run_sweep

    summary = run_sweep(
        goals_root=goals_dir,
        output_dir=output_dir,
        dimensions=dimensions,
        config_path=args.config,
        loop_config=loop_config,
    )

    _write_report(summary, output_dir)
    logger.info("=== Field test run complete %s ===", datetime.now(UTC).isoformat())
    fh.flush()

    if summary["failed"] > 0:
        logger.warning("%d execution(s) failed — see report for details", summary["failed"])
        return 1
    logger.info("All %d executions passed", summary["passed"])
    return 0


__all__ = ["build_field_test_parser", "run_field_test"]
