from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..corpus import load_all_instances, load_corpus_manifest
from ..eval.regression import generate_artifact
from ..gates import run_deterministic_gates
from ..types import Severity


def build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic eval",
        description="Security oracle evaluation commands",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="eval_command", metavar="COMMAND", required=True)

    swebench = sub.add_parser(
        "swebench-security",
        help="Run critic oracle eval against the SWE-bench security corpus",
    )
    swebench.add_argument(
        "--corpus-dir",
        default=str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "docs"
            / "field-test"
            / "corpus"
            / "swebench-security"
        ),
        help="Path to the corpus directory",
    )
    swebench.add_argument(
        "--instance-ids",
        nargs="*",
        default=None,
        help="Specific instance IDs to evaluate (default: all)",
    )
    swebench.add_argument(
        "--report-dir",
        default=None,
        help="Directory to write report JSON (default: print to stdout)",
    )
    swebench.add_argument(
        "--adversarial",
        action="store_true",
        help="Run adversarial injection harness after oracle eval",
    )
    swebench.add_argument(
        "--regression",
        action="store_true",
        help="Run deterministic-gate regression against the corpus",
    )
    return parser


def _run_regression_eval(corpus_dir: str) -> dict[str, Any]:
    instances = load_all_instances(corpus_dir)
    results: list[dict[str, Any]] = []

    for inst in instances:
        artifact = generate_artifact(inst)
        correct_findings = run_deterministic_gates(artifact.correct)
        correct_blockers = [f for f in correct_findings if f.severity is Severity.BLOCKER]

        variant_results: list[dict[str, Any]] = []
        for i, variant in enumerate(artifact.variants):
            findings = run_deterministic_gates(variant)
            blockers = [f for f in findings if f.severity is Severity.BLOCKER]
            expected = (
                str(artifact.variant_expected[i]) if i < len(artifact.variant_expected) else ""
            )
            label = (
                artifact.variant_labels[i] if i < len(artifact.variant_labels) else f"variant-{i}"
            )
            variant_results.append(
                {
                    "label": label,
                    "expected_reason_code": expected,
                    "blocker_count": len(blockers),
                    "blocker_reason_codes": [str(f.reason_code) for f in blockers],
                    "passed": any(str(f.reason_code) == expected for f in blockers)
                    if expected
                    else len(blockers) == 0,
                }
            )

        results.append(
            {
                "instance_id": inst.instance_id,
                "correct_passes": len(correct_blockers) == 0,
                "correct_blocker_count": len(correct_blockers),
                "variant_count": len(artifact.variants),
                "variants": variant_results,
            }
        )

    total = len(results)
    correct_pass = sum(1 for r in results if r["correct_passes"])
    variants_total = sum(r["variant_count"] for r in results)
    variants_pass = sum(1 for r in results for v in r["variants"] if v["passed"])

    return {
        "total_instances": total,
        "correct_plans_pass": correct_pass,
        "correct_plans_total": total,
        "variants_total": variants_total,
        "variants_passed": variants_pass,
        "results": results,
    }


def run_eval(argv: list[str]) -> int:
    parser = build_eval_parser()
    args = parser.parse_args(argv)

    if args.eval_command == "swebench-security":
        manifest = load_corpus_manifest(args.corpus_dir)
        if manifest is None:
            print(f"corpus manifest not found in {args.corpus_dir}")
            return 1

        print(f"Corpus: {manifest.name} v{manifest.version} ({manifest.instance_count} instances)")

        if args.regression:
            print("\n--- Deterministic Gate Regression ---")
            reg_results = _run_regression_eval(args.corpus_dir)
            print(
                f"Correct plans passing: {reg_results['correct_plans_pass']}"
                f"/{reg_results['correct_plans_total']}"
            )
            print(
                f"Flawed variants blocked: {reg_results['variants_passed']}"
                f"/{reg_results['variants_total']}"
            )
            for r in reg_results["results"]:
                for v in r["variants"]:
                    status = "PASS" if v["passed"] else "FAIL"
                    print(
                        f"  [{status}] {r['instance_id']}: {v['label']} "
                        f"-> {v['blocker_reason_codes']}"
                    )
            if args.report_dir:
                report_path = Path(args.report_dir) / "regression-report.json"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(reg_results, indent=2))
                print(f"Regression report: {report_path}")

        if args.adversarial:
            print("\n--- Adversarial Injection Harness ---")
            print("  (requires configured planner+critic roles; use scripted roles for hermetic)")
            print("  Use OracleEvalHarness or run_injection_harness() directly for full eval.")
            from ..eval.injection import generate_traps

            instances = load_all_instances(args.corpus_dir)
            if args.instance_ids:
                instances = [i for i in instances if i.instance_id in args.instance_ids]
            total_traps = 0
            for inst in instances:
                traps = generate_traps(inst)
                total_traps += len(traps)
                for t in traps:
                    print(f"  {t.trap_id}: {t.trap_type.value}")
            print(f"  ({total_traps} traps generated from {len(instances)} instances)")

        if not args.regression and not args.adversarial:
            print("Run with --regression and/or --adversarial flags.")
            return 0

        return 0

    parser.print_help()
    return 1


__all__ = [
    "build_eval_parser",
    "run_eval",
]
