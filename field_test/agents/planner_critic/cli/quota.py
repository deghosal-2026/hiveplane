from __future__ import annotations

import argparse
import json

from ..quota import BlastRadiusQuotaConfig


def build_quota_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic quota",
        description="Manage blast-radius quotas (M6)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="quota_command", required=True)

    list_parser = sub.add_parser("list", help="List current quotas")
    list_parser.add_argument("--domain", default=None, help="Domain name")

    set_parser = sub.add_parser("set", help="Set a quota value")
    set_parser.add_argument(
        "key",
        choices=[
            "max_resource_changes",
            "max_destructive_actions",
            "max_database_alterations",
        ],
    )
    set_parser.add_argument("value", type=int)
    set_parser.add_argument("--domain", default=None)

    check_parser = sub.add_parser("check", help="Dry-run a plan against quotas")
    check_parser.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    check_parser.add_argument("--quotas", required=True, help="Path to quotas YAML file")

    return parser


def run_quota(argv: list[str]) -> int:
    args = build_quota_parser().parse_args(argv)

    if args.quota_command == "list":
        print(f"Quotas for domain={args.domain or 'default'}:")
        print("  (run 'plancritic quota set <key> <value>' to configure)")
        return 0

    if args.quota_command == "set":
        print(f"Set {args.key}={args.value}")
        return 0

    if args.quota_command == "check":
        from pathlib import Path

        import yaml

        from ..schema.plan import PlanVersion

        try:
            quotas_data = yaml.safe_load(Path(args.quotas).read_text())
            config = BlastRadiusQuotaConfig.from_dict(quotas_data.get("quotas", {}))
        except Exception as err:
            print(f"failed to load quotas: {err}")
            return 1

        try:
            plan_data = json.loads(Path(args.plan_file).read_text())
            plan = PlanVersion.model_validate(plan_data)
        except Exception as err:
            print(f"failed to load plan: {err}")
            return 1

        from ..quota import BlastRadiusQuotaGate

        gate = BlastRadiusQuotaGate(config)
        findings = gate.run(plan)

        if not findings:
            print("plan passes all quotas ✓")
            return 0

        for f in findings:
            print(f"  [{f.severity.value}] {f.reason_code}: {f.message} (task={f.task_id})")
        return 1

    return 0


__all__ = ["build_quota_parser", "run_quota"]
