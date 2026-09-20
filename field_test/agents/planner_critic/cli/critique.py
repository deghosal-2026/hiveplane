from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..gates import run_deterministic_gates
from ..schema.plan import PlanVersion
from ..store.sqlite import SQLiteStore

DEFAULT_STORE_PATH = ".plancritic/plans.db"


def build_critique_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic critique",
        description="Run deterministic gates on a plan (F-61)",
        add_help=False,
    )
    parser.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    parser.add_argument("--store", default=DEFAULT_STORE_PATH, help="Store path")
    return parser


def run_critique(argv: list[str]) -> int:
    args = build_critique_parser().parse_args(argv)
    try:
        plan_data = json.loads(Path(args.plan_file).read_text())
    except FileNotFoundError:
        print(f"plan file not found: {args.plan_file}")
        return 1
    except json.JSONDecodeError as err:
        print(f"invalid plan JSON: {err}")
        return 1

    try:
        plan = PlanVersion.from_dict(plan_data)
    except Exception as err:
        print(f"plan validation failed: {err}")
        return 1

    findings = run_deterministic_gates(plan)

    if not findings:
        print("no findings — plan passes all deterministic gates")
    else:
        for f in findings:
            task = f" [{f.task_id}]" if f.task_id else ""
            print(f"[{f.severity.value}]{task} {f.reason_code}: {f.message}")

    try:
        store = SQLiteStore(args.store)
        store.put_findings(plan.id, plan.version, findings)
        store.close()
    except Exception as err:
        print(f"warning: could not store findings: {err}")

    blocker_count = sum(1 for f in findings if f.severity.value == "blocker")
    if blocker_count:
        print(f"\n{blocker_count} blocker(s) found — plan cannot be approved")
        return 1
    return 0


__all__ = ["build_critique_parser", "run_critique"]
