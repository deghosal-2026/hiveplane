from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..store.base import PlanStore
from ..store.sqlite import SQLiteStore
from ..types import ExecutionTrace

DIAGNOSTIC_RULES: list[dict[str, Any]] = [
    {
        "match": {"failure_class": "planning", "reason_code": "missing_rollback"},
        "category": "precondition.missing_rollback",
        "severity": 4,
        "root_cause_template": "The Planner omitted a rollback step "
        "for a high-risk action ({action}). "
        "The plan assumed the action would succeed, but had no recovery path.",
        "suggested_fix_template": "Add a rollback step before the high-risk action. "
        "Enable the domain pack's rollback gate enforcement.",
    },
    {
        "match": {"failure_class": "planning", "reason_code": "missing_verification"},
        "category": "precondition.missing_verification",
        "severity": 3,
        "root_cause_template": "The Planner did not include a verification step after {action}. "
        "Without verification, the execution cannot confirm success.",
        "suggested_fix_template": "Add a verification step after the action. "
        "Enable enforcement of the verification gate.",
    },
    {
        "match": {"failure_class": "planning", "reason_code": "unverified_precondition"},
        "category": "precondition.unverified",
        "severity": 3,
        "root_cause_template": "A precondition ({precondition}) was not "
        "established before the step. "
        "The plan assumed the environment state without verifying it.",
        "suggested_fix_template": "Add an EnvProbe or earlier task that "
        "establishes the precondition. "
        "Enable the persistent precondition ledger (M6.4) to prevent compaction-induced loss.",
    },
    {
        "match": {"failure_class": "execution", "reason_code": "transient_retry_triggered"},
        "category": "execution.transient_network",
        "severity": 2,
        "root_cause_template": "The execution encountered a transient network "
        "error on step {step}. "
        "The plan is correct; the environment was temporarily unavailable.",
        "suggested_fix_template": "No plan fix needed. Consider increasing the step retry budget "
        "(step_max_retries) if transient errors are frequent.",
    },
    {
        "match": {"failure_class": "execution", "reason_code": "state_view_stale"},
        "category": "state.snapshot_stale",
        "severity": 3,
        "root_cause_template": "The environment state changed between plan "
        "approval and step execution. "
        "The snapshot taken at approval time no longer matches live state.",
        "suggested_fix_template": "Enable the re-gate (F-46) to detect stale "
        "state and trigger a replan. "
        "Reduce approval_ttl to minimize the window for state drift.",
    },
    {
        "match": {
            "failure_class": "execution",
            "reason_code": "resource_locked_by_concurrent_execution",
        },
        "category": "state.resource_locked",
        "severity": 4,
        "root_cause_template": "The resource was locked by another concurrent agent execution. "
        "Two agents tried to mutate the same resource simultaneously.",
        "suggested_fix_template": "Adjust the StateLock strategy to 'wait' with a longer timeout, "
        "or serialize plans targeting the same resource. "
        "Enable pre-execution conflict detection (M6.3).",
    },
    {
        "match": {"failure_class": "execution", "outcome": "timeout"},
        "category": "execution.timeout",
        "severity": 2,
        "root_cause_template": "The execution timed out on step {step}. "
        "The operation took longer than the configured timeout.",
        "suggested_fix_template": "Increase the step timeout or break the task "
        "into smaller sub-tasks. "
        "Check if the target system is under load.",
    },
    {
        "match": {"failure_class": "execution", "outcome__contains": "auth_failure"},
        "category": "execution.authentication",
        "severity": 4,
        "root_cause_template": "Authentication failed during step {step}. "
        "The credentials or permissions assumed by the plan were not available at execution time.",
        "suggested_fix_template": "Verify the execution context has the required credentials. "
        "Add a precondition that checks authentication before the step.",
    },
    {
        "match": {"failure_class": "execution", "outcome__contains": "permission_denied"},
        "category": "execution.authorization",
        "severity": 4,
        "root_cause_template": "Permission denied on step {step}. "
        "The execution identity lacks the required IAM/permissions for the action.",
        "suggested_fix_template": "Add a precondition to verify permissions before the step. "
        "Check that the execution role has the required policies.",
    },
    {
        "match": {"failure_class": "planning", "reason_code": "blast_radius_quota_breach"},
        "category": "quota.blast_radius",
        "severity": 4,
        "root_cause_template": "The plan breached a blast-radius quota "
        "(resource/action count limit). "
        "The plan attempted to modify more resources than the configured ceiling.",
        "suggested_fix_template": "Split the plan into smaller batches, or "
        "increase the quota ceiling "
        "via 'plancritic quota set' with appropriate approval.",
    },
]


def _match_rule(
    rule: dict[str, Any], trace: ExecutionTrace, plan_data: dict[str, Any] | None
) -> bool:
    match = rule["match"]
    for key, expected in match.items():
        if key == "outcome__contains":
            actual = (trace.outcome or "").lower()
            if expected not in actual:
                return False
        elif key == "outcome":
            actual = (trace.outcome or "").lower()
            if actual != expected:
                return False
        elif key == "failure_class":
            actual = (trace.failure_class or "").lower() if trace.failure_class else ""
            if actual != expected:
                return False
        elif key == "reason_code":
            actual = (trace.outcome or "").lower()
            if expected not in actual:
                return False
        else:
            return False
    return True


def _diagnose_trace(
    trace: ExecutionTrace, plan_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    for rule in DIAGNOSTIC_RULES:
        if _match_rule(rule, trace, plan_data):
            step_info = {
                "step": trace.task_id,
                "action": trace.outcome or "unknown",
                "precondition": "unknown",
            }
            root_cause = rule["root_cause_template"].format(**step_info)
            suggested_fix = rule["suggested_fix_template"].format(**step_info)
            return {
                "failing_step": trace.task_id,
                "failure_class": trace.failure_class,
                "outcome": trace.outcome,
                "category": rule["category"],
                "severity": rule["severity"],
                "root_cause": root_cause,
                "suggested_fix": suggested_fix,
                "trace_excerpt": trace.model_dump(mode="json"),
            }
    return {
        "failing_step": trace.task_id,
        "failure_class": trace.failure_class,
        "outcome": trace.outcome,
        "category": "unclassified_failure",
        "severity": 1,
        "root_cause": (
            "No diagnostic rule matched this execution trace. Review the raw trace manually."
        ),
        "suggested_fix": None,
        "trace_excerpt": trace.model_dump(mode="json"),
    }


def _format_human(diag: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Failing step:  {diag['failing_step']}")
    lines.append(f"Failure class: {diag['failure_class'] or 'unknown'}")
    lines.append(f"Outcome:       {diag['outcome'] or 'unknown'}")
    lines.append(f"Category:      {diag['category']}")
    lines.append(f"Severity:      {diag['severity']}/5")
    lines.append("")
    lines.append(f"Root cause:    {diag['root_cause']}")
    if diag.get("suggested_fix"):
        lines.append(f"Suggested fix: {diag['suggested_fix']}")
    lines.append("")
    lines.append("Trace excerpt:")
    ex = diag.get("trace_excerpt", {})
    if isinstance(ex, dict):
        for k, v in ex.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _format_markdown(diag: dict[str, Any]) -> str:
    lines = []
    lines.append("## Execution Trace Diagnosis")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Failing step | {diag['failing_step']} |")
    lines.append(f"| Failure class | {diag['failure_class'] or 'unknown'} |")
    lines.append(f"| Outcome | {diag['outcome'] or 'unknown'} |")
    lines.append(f"| Category | {diag['category']} |")
    lines.append(f"| Severity | {diag['severity']}/5 |")
    lines.append("")
    lines.append(f"**Root cause:** {diag['root_cause']}")
    if diag.get("suggested_fix"):
        lines.append("")
        lines.append(f"**Suggested fix:** {diag['suggested_fix']}")
    return "\n".join(lines)


def build_diagnose_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic diagnose",
        description="Sentry-style root-cause analyzer for execution traces (M7)",
        add_help=False,
    )
    parser.add_argument("trace_source", help="Execution trace JSON file path or plan_id")
    parser.add_argument("--store", default=None, help="SQLite store path (for plan_id lookup)")
    parser.add_argument(
        "--format", default="human", choices=["human", "json", "markdown"], help="Output format"
    )
    parser.add_argument(
        "--export-otel", action="store_true", help="Emit diagnosis as OTel span attributes"
    )
    return parser


def run_diagnose(argv: list[str]) -> int:
    args = build_diagnose_parser().parse_args(argv)

    trace_path = Path(args.trace_source)
    if trace_path.exists():
        try:
            data = json.loads(trace_path.read_text())
            if isinstance(data, list):
                traces = [ExecutionTrace.model_validate(t) for t in data]
            else:
                traces = [ExecutionTrace.model_validate(data)]
        except Exception as err:
            print(f"error: failed to load trace: {err}", file=sys.stderr)
            return 1
    else:
        try:
            store: PlanStore = SQLiteStore(args.store or ".plancritic/plans.db")
            traces = store.get_execution_traces(args.trace_source)
            store.close()
        except Exception as err:
            print(f"error: failed to load traces from store: {err}", file=sys.stderr)
            return 1

    if not traces:
        print("No execution traces found.", file=sys.stderr)
        return 1

    diagnoses = [_diagnose_trace(t) for t in traces]

    if args.format == "json":
        print(json.dumps(diagnoses, indent=2))
    elif args.format == "markdown":
        for d in diagnoses:
            print(_format_markdown(d))
            print("---")
    else:
        for d in diagnoses:
            print(_format_human(d))
            print("---")

    if args.export_otel:
        try:
            from opentelemetry import trace as otel_trace

            tracer = otel_trace.get_tracer("planner_critic.diagnose")
            for d in diagnoses:
                with tracer.start_as_current_span("diagnose") as span:
                    span.set_attribute("diagnose.failing_step", d["failing_step"])
                    span.set_attribute("diagnose.category", d["category"])
                    span.set_attribute("diagnose.severity", d["severity"])
                    span.set_attribute("diagnose.root_cause", d["root_cause"])
        except ImportError:
            pass

    return 0


__all__ = ["build_diagnose_parser", "run_diagnose"]
