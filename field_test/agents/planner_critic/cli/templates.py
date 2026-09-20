from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..loop.autofix import SEED_TEMPLATES, PreconditionTemplate
from ..schema.plan import PlanVersion


def _clean_template(t: PreconditionTemplate) -> dict[str, object]:
    return {
        "name": t.name,
        "pattern": t.pattern,
        "task_fields": dict(t.task_fields),
    }


def build_templates_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic templates",
        description="Manage precondition templates (M7)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="templates_command", required=True)

    sub.add_parser("list", help="List installed precondition templates")
    add = sub.add_parser("add", help="Register a new precondition template")
    add.add_argument("name", help="Template name")
    add.add_argument("--pattern", required=True, help="Pattern to match against precondition fact")
    add.add_argument("--task-id", default=None, help="Task id for the injected step")
    add.add_argument("--description", default=None, help="Task description")
    add.add_argument("--action", default="do", help="Action for the injected step")
    add.add_argument("--target", default=None, help="Target for the injected step")
    add.add_argument(
        "--risk-class",
        default="low",
        choices=["low", "medium", "high", "critical"],
        help="Risk class",
    )
    test = sub.add_parser("test", help="Dry-run the closer against a sample plan")
    test.add_argument("name", help="Template name")
    test.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    return parser


def run_templates(argv: list[str]) -> int:
    args = build_templates_parser().parse_args(argv)

    if args.templates_command == "list":
        if not SEED_TEMPLATES:
            print("No precondition templates installed.")
            return 0
        print(f"Precondition templates ({len(SEED_TEMPLATES)}):")
        for t in SEED_TEMPLATES:
            fields = dict(t.task_fields)
            desc = fields.get("description", "")
            print(f"  {t.name}")
            print(f"    Pattern: {t.pattern}")
            print(f"    Injects: {desc}")
            print(f"    Action: {fields.get('action', 'do')} -> {fields.get('target', '?')}")
        return 0

    if args.templates_command == "add":
        task_fields: dict[str, object] = {
            "id": args.task_id or args.name,
            "description": args.description or f"Auto-injected: {args.pattern}",
            "action": args.action,
            "target": args.target or args.pattern,
            "risk_class": args.risk_class,
        }
        template = PreconditionTemplate(
            name=args.name,
            pattern=args.pattern,
            task_fields=task_fields,
        )
        print(f"Registered precondition template: {template.name}")
        print(f"  Pattern: {template.pattern}")
        return 0

    if args.templates_command == "test":
        matching = [t for t in SEED_TEMPLATES if t.name == args.name]
        if not matching:
            print(f"error: template {args.name!r} not found", file=sys.stderr)
            return 1

        try:
            data = json.loads(Path(args.plan_file).read_text())
            plan = PlanVersion.model_validate(data)
        except Exception as err:
            print(f"error: failed to load plan: {err}", file=sys.stderr)
            return 1

        from ..gates import run_deterministic_gates
        from ..loop.autofix import apply_precondition_closer

        gate_findings = run_deterministic_gates(plan)
        closed_plan, findings = apply_precondition_closer(plan, gate_findings)
        if closed_plan is None:
            print(f"Template {args.name}: did not trigger (no matching precondition gap)")
        else:
            print(f"Template {args.name}: triggered — closed precondition gap")
            print(f"  Plan now has {len(closed_plan.tasks)} tasks (was {len(plan.tasks)})")
            for f in findings:
                print(f"  [{f.severity.value}] {f.reason_code}: {f.message}")
        return 0

    return 0


__all__ = ["build_templates_parser", "run_templates"]
