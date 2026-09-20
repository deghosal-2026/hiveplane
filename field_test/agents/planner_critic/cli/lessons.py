from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..corpus import load_all_instances
from ..eval.standing_rules import StandingRuleRegistry


def build_lessons_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic lessons",
        description="Manage standing-rule candidates from missed-critique analysis (M5)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="lessons_command", required=True)

    propose = sub.add_parser("propose", help="Propose standing-rule candidates from corpus misses")
    propose.add_argument("--corpus-dir", default=None, help="Corpus directory")
    propose.add_argument(
        "--instance-ids", nargs="*", default=None, help="Filter to specific instances"
    )

    list_parser = sub.add_parser("list", help="List proposed/promoted standing rules")
    list_parser.add_argument(
        "--status", default=None, choices=["proposed", "promoted"], help="Filter by status"
    )

    promote = sub.add_parser("promote", help="Promote a standing rule to the heuristic pack")
    promote.add_argument("rule_id", help="Rule ID to promote")

    return parser


def run_lessons(argv: list[str]) -> int:
    parser = build_lessons_parser()
    args = parser.parse_args(argv)

    if args.lessons_command == "propose":
        corpus_dir = args.corpus_dir or str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "docs"
            / "field-test"
            / "corpus"
            / "swebench-security"
        )
        instances = load_all_instances(corpus_dir)
        if args.instance_ids:
            instances = [i for i in instances if i.instance_id in args.instance_ids]

        registry = StandingRuleRegistry()
        total = 0
        for inst in instances:
            bucket = (
                inst.cwe_bucket.value if hasattr(inst.cwe_bucket, "value") else str(inst.cwe_bucket)
            )
            codes = inst.expected_reason_codes or []
            proposed = registry.propose_from_misses(
                cwe_bucket=bucket,
                instance_ids=[inst.instance_id],
                missed_reason_codes=codes,
            )
            total += len(proposed)

        print(f"Proposed {total} new standing-rule candidates from {len(instances)} instances.")
        for rule in registry.list_rules(status="proposed"):
            print(
                f"  {rule.rule_id} ({rule.cwe_bucket}, {rule.pattern})"
                f" — coverage={rule.coverage_count}"
            )
        return 0

    if args.lessons_command == "list":
        registry = StandingRuleRegistry()
        rules = registry.list_rules(status=args.status)
        if not rules:
            print("No standing rules found.")
            return 0
        print(f"Standing rules ({len(rules)}):")
        for rule in rules:
            status = rule.status
            trust = rule.trust
            print(f"  {rule.rule_id}")
            print(f"    CWE: {rule.cwe_bucket} | Pattern: {rule.pattern}")
            print(f"    Family: {rule.heuristic_family} | Code: {rule.reason_code}")
            print(f"    Status: {status} | Trust: {trust} | Coverage: {rule.coverage_count}")
            print(f"    Sources: {rule.source_instance_ids}")
        return 0

    if args.lessons_command == "promote":
        registry = StandingRuleRegistry()
        if registry.promote(args.rule_id):
            print(f"Promoted {args.rule_id} to standing rule.")
        else:
            print(f"error: rule {args.rule_id!r} not found or already promoted", file=sys.stderr)
            return 1
        return 0

    return 0


__all__ = ["build_lessons_parser", "run_lessons"]
