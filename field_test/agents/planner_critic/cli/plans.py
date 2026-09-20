from __future__ import annotations

import argparse
import json

from ..store.base import StoreUnavailable
from ..store.sqlite import SQLiteStore
from ..viz.graph import to_mermaid

DEFAULT_STORE_PATH = ".plancritic/plans.db"


def build_plans_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic plans",
        description="Manage stored plans (F-61)",
        add_help=False,
    )
    parser.add_argument("--store", default=DEFAULT_STORE_PATH, help="SQLite store path")
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    sub.add_parser("list", help="List stored plans")

    show = sub.add_parser("show", help="Show plan details")
    show.add_argument("plan_id", help="Plan id")
    show.add_argument("--version", type=int, default=None, help="Revision number (default: latest)")

    diff = sub.add_parser("diff", help="Show diff between plan versions")
    diff.add_argument("plan_id", help="Plan id")
    diff.add_argument("v1", type=int, help="Older version")
    diff.add_argument("v2", type=int, help="Newer version")
    diff.add_argument("--graph", action="store_true", help="Render v2 as Mermaid")

    return parser


def run_plans(argv: list[str]) -> int:
    args = build_plans_parser().parse_args(argv)
    try:
        store = SQLiteStore(args.store)
        try:
            if args.action == "list":
                return _run_list(store)
            if args.action == "show":
                return _run_show(store, args.plan_id, args.version)
            if args.action == "diff":
                return _run_diff(store, args.plan_id, args.v1, args.v2, args.graph)
            print("usage: plancritic plans list|show|diff ...")
            return 1
        finally:
            store.close()
    except StoreUnavailable as err:
        print(f"plans failed: {err}")
        return 1


def _run_list(store: SQLiteStore) -> int:
    plans = store.list_plans()
    if not plans:
        print("no plans stored")
        return 0
    seen_ids: set[str] = set()
    for p in plans:
        if p.id not in seen_ids:
            print(f"{p.id} (latest v{p.version}, goal={p.goal_id})")
            seen_ids.add(p.id)
    return 0


def _run_show(store: SQLiteStore, plan_id: str, version: int | None) -> int:
    plan = store.get_plan(plan_id, version)
    if plan is None:
        print(f"plan {plan_id!r} not found" + (f" (version {version})" if version else ""))
        return 1
    print(json.dumps(plan.to_dict(), indent=2))
    return 0


def _run_diff(store: SQLiteStore, plan_id: str, v1: int, v2: int, graph: bool) -> int:
    diff = store.diff(plan_id, v1, v2)
    if diff is None:
        print(f"diff not available — one or both revisions not found for plan {plan_id!r}")
        return 1

    print(f"diff {plan_id} v{diff.from_version} → v{diff.to_version}")
    if diff.is_empty:
        print("  (no structural changes)")
        return 0

    if diff.added_task_ids:
        print(f"  added tasks:       {', '.join(diff.added_task_ids)}")
    if diff.removed_task_ids:
        print(f"  removed tasks:     {', '.join(diff.removed_task_ids)}")
    if diff.changed_task_ids:
        print(f"  changed tasks:     {', '.join(diff.changed_task_ids)}")
    if diff.added_dependencies:
        print(
            "  added dependencies: "
            + ", ".join(f"{d.from_task}->{d.to_task}" for d in diff.added_dependencies)
        )
    if diff.removed_dependencies:
        print(
            "  removed dependencies: "
            + ", ".join(f"{d.from_task}->{d.to_task}" for d in diff.removed_dependencies)
        )
    if diff.added_branches:
        print(f"  added branches:    {', '.join(diff.added_branches)}")
    if diff.removed_branches:
        print(f"  removed branches:  {', '.join(diff.removed_branches)}")

    if graph:
        v2_plan = store.get_plan(plan_id, v2)
        if v2_plan is not None:
            print("\n--- Mermaid DAG (v2) ---")
            print(to_mermaid(v2_plan))

    return 0


__all__ = ["build_plans_parser", "run_plans"]
