from __future__ import annotations

import argparse
import json

from ..store.base import StoreUnavailable
from ..store.sqlite import SQLiteStore


def build_findings_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic findings",
        description="Query plan findings with drift observability (M8)",
        add_help=False,
    )
    parser.add_argument("plan_id", help="Plan id to query findings for")
    parser.add_argument(
        "--version", type=int, default=None, help="Revision number (default: latest)"
    )
    parser.add_argument("--store", default=".plancritic/plans.db", help="SQLite store path")
    parser.add_argument(
        "--include-raw", action="store_true", help="Show raw_severity and drift_delta"
    )
    parser.add_argument("--output", default="text", choices=["text", "json"], help="Output format")
    return parser


def run_findings(argv: list[str]) -> int:
    parser = build_findings_parser()
    args = parser.parse_args(argv)

    try:
        store = SQLiteStore(args.store)
    except StoreUnavailable as err:
        print(f"store error: {err}")
        return 1

    try:
        plan = store.get_plan(args.plan_id, args.version)
        if plan is None:
            print(
                f"plan {args.plan_id!r} not found"
                + (f" (version {args.version})" if args.version else "")
            )
            return 1

        version = args.version if args.version is not None else plan.version
        findings = store.get_findings(args.plan_id, version)

        if args.output == "json":
            output: list[dict[str, object]] = []
            for f in findings:
                entry: dict[str, object] = {
                    "id": f.id,
                    "task_id": f.task_id,
                    "severity": f.severity.value,
                    "reason_code": f.reason_code,
                    "message": f.message,
                    "heuristic_family": f.heuristic_family.value if f.heuristic_family else None,
                }
                if args.include_raw:
                    entry["raw_severity"] = f.raw_severity.value if f.raw_severity else None
                    entry["normalized_severity"] = (
                        f.normalized_severity.value if f.normalized_severity else None
                    )
                    entry["drift_delta"] = f.drift_delta
                output.append(entry)
            print(json.dumps(output, indent=2))
        else:
            print(f"Findings for {args.plan_id} v{version} ({len(findings)} total):")
            for f in findings:
                sev = f.severity.value.upper()
                family = f" [{f.heuristic_family.value}]" if f.heuristic_family else ""
                line = f"  [{sev}]{family} {f.reason_code}: {f.message} (task={f.task_id})"
                if args.include_raw and f.raw_severity is not None:
                    norm = f.normalized_severity.value if f.normalized_severity else "?"
                    drift = f" (raw: {f.raw_severity.value} -> norm: {norm}, delta={f.drift_delta})"
                    line += drift
                print(line)

        drift_count = sum(1 for f in findings if f.drift_delta != 0)
        if drift_count > 0:
            print(f"\nDrift: {drift_count}/{len(findings)} findings have drift (delta != 0)")

        return 0
    finally:
        store.close()


__all__ = ["build_findings_parser", "run_findings"]
