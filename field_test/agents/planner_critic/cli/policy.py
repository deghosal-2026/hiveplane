from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..policy import BUILTIN_POLICIES, CelGate, PolicyEngine, RegoGate
from ..schema.plan import PlanVersion


def _rego_policies_from_dir(pol_dir: Path) -> list[PolicyEngine]:
    gates: list[PolicyEngine] = []
    for entry in sorted(pol_dir.iterdir()):
        if entry.suffix == ".rego":
            gates.append(
                RegoGate(
                    name=entry.stem,
                    module=entry,
                    query="data.test.violation",
                )
            )
        elif entry.suffix in (".yaml", ".yml"):
            import yaml

            try:
                data = yaml.safe_load(entry.read_text())
                if isinstance(data, dict) and data.get("kind") == "Policy":
                    expr = data.get("cel", "")
                    if expr:
                        gates.append(
                            CelGate(
                                name=data.get("name", entry.stem),
                                expression=expr,
                                severity=data.get("severity", "blocker"),
                                message=data.get("message"),
                            )
                        )
            except Exception:  # noqa: S110  # best-effort gate load must not abort
                pass
    return gates


def build_policy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic policy",
        description="Manage policies (M7)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="policy_command", required=True)

    sub.add_parser("list", help="List installed policy gates")
    add = sub.add_parser("add", help="Register a policy")
    add.add_argument("path", help="Path to a .rego, policy-pack.yaml, or .yaml file")
    test = sub.add_parser("test", help="Dry-run a policy against a plan")
    test.add_argument("name", help="Policy name or file path")
    test.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    return parser


def run_policy(argv: list[str]) -> int:
    args = build_policy_parser().parse_args(argv)

    if args.policy_command == "list":
        count = len(BUILTIN_POLICIES)
        print(f"Built-in policies ({count}):")
        for p in BUILTIN_POLICIES:
            print(f"  {p.name} (severity={p.severity.value})")
        print()
        print("Use 'plancritic policy add <path>' to register custom policies.")
        return 0

    if args.policy_command == "add":
        path = Path(args.path)
        if not path.exists():
            print(f"error: not found: {args.path}", file=sys.stderr)
            return 1
        try:
            policies = (
                _rego_policies_from_dir(path)
                if path.is_dir()
                else [
                    CelGate(
                        name=path.stem,
                        expression=path.read_text().strip(),
                        severity="blocker",
                    )
                ]
            )
            for p in policies:
                print(f"Registered policy: {p.name}")
        except Exception as err:
            print(f"error: failed to load policy: {err}", file=sys.stderr)
            return 1
        return 0

    if args.policy_command == "test":
        try:
            pol_path = Path(args.name)
            if pol_path.exists():
                policies = (
                    _rego_policies_from_dir(pol_path)
                    if pol_path.is_dir()
                    else [
                        CelGate(
                            name=pol_path.stem,
                            expression=pol_path.read_text().strip(),
                            severity="blocker",
                        )
                    ]
                )
            else:
                policies = [p for p in BUILTIN_POLICIES if p.name == args.name]
                if not policies:
                    print(f"error: policy {args.name!r} not found", file=sys.stderr)
                    return 1
        except Exception as err:
            print(f"error: failed to load policy: {err}", file=sys.stderr)
            return 1

        try:
            data = json.loads(Path(args.plan_file).read_text())
            plan = PlanVersion.model_validate(data)
        except Exception as err:
            print(f"error: failed to load plan: {err}", file=sys.stderr)
            return 1

        all_pass = True
        for policy in policies:
            findings = policy.evaluate(plan)
            if findings:
                all_pass = False
                for f in findings:
                    print(f"  [{f.severity.value}] {policy.name}: {f.message}")
            else:
                print(f"  [pass] {policy.name}")

        return 0 if all_pass else 1

    return 0


__all__ = ["build_policy_parser", "run_policy"]
