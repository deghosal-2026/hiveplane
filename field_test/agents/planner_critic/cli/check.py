from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..domains.base import find_domain_packs, load_domain_pack_from_manifest
from ..gates import run_deterministic_gates
from ..gates.base import BaseGate
from ..policy import BUILTIN_POLICIES, CelGate, PolicyEngine, RegoGate
from ..schema.plan import PlanVersion
from ..types import Finding, Severity


def _load_plan(path: str) -> PlanVersion | None:
    try:
        data = json.loads(Path(path).read_text())
        return PlanVersion.model_validate(data)
    except Exception as err:
        print(f"error: failed to load plan: {err}", file=sys.stderr)
        return None


def _gate_verdict(findings: list[Finding], fail_severity: Severity) -> tuple[bool, list[Finding]]:
    order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.BLOCKER: 2}
    threshold = order.get(fail_severity, 2)
    failures = [f for f in findings if order.get(f.severity, 0) >= threshold]
    return (len(failures) == 0, failures)


def build_check_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic check",
        description="Lightweight deterministic gate evaluation against a plan file (zero LLM)",
        add_help=False,
    )
    parser.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    parser.add_argument(
        "--domain", default=None, help="Load gates from a domain pack by name or manifest path"
    )
    parser.add_argument(
        "--policies-dir", default=None, help="Directory containing .rego policy files"
    )
    parser.add_argument(
        "--enforcement",
        default="strict",
        choices=["strict", "permissive", "dry_run"],
        help="Gate enforcement mode",
    )
    parser.add_argument(
        "--context", action="append", default=[], help="Key=value execution context (repeatable)"
    )
    parser.add_argument(
        "--fail-on-severity",
        default="high",
        choices=["low", "medium", "high", "critical", "blocker", "warning"],
        help="Minimum severity to yield non-zero exit",
    )
    parser.add_argument(
        "--output", default="text", choices=["text", "json", "yaml"], help="Output format"
    )
    return parser


def run_check(argv: list[str]) -> int:
    args = build_check_parser().parse_args(argv)

    plan = _load_plan(args.plan_file)
    if plan is None:
        return 4

    extra_gates: list[BaseGate] = []
    extra_policies: list[PolicyEngine] = []
    has_domain = False

    if args.domain:
        try:
            from ..domains.base import DomainPack

            domain_path = Path(args.domain)
            pack: DomainPack
            if domain_path.exists():
                pack = load_domain_pack_from_manifest(domain_path)
            else:
                packs = find_domain_packs()
                candidate = packs.get(args.domain)
                if candidate is None:
                    print(f"error: domain pack {args.domain!r} not found", file=sys.stderr)
                    return 4
                pack = candidate
            extra_gates.extend(pack.gate_evaluators)
            has_domain = True
        except Exception as err:
            print(f"error: failed to load domain pack: {err}", file=sys.stderr)
            return 4

    if args.policies_dir:
        pol_dir = Path(args.policies_dir)
        if not pol_dir.is_dir():
            print(f"error: policies dir not found: {args.policies_dir}", file=sys.stderr)
            return 4
        for entry in sorted(pol_dir.iterdir()):
            if entry.suffix == ".rego":
                policy: PolicyEngine = RegoGate(
                    name=entry.stem,
                    module=entry,
                    query="data.test.violation",
                )
                extra_policies.append(policy)
            elif entry.suffix in (".yaml", ".yml"):
                try:
                    import yaml

                    data = yaml.safe_load(entry.read_text())
                    if isinstance(data, dict) and data.get("kind") == "Policy":
                        expr = data.get("cel", "")
                        if expr:
                            policy = CelGate(
                                name=data.get("name", entry.stem),
                                expression=expr,
                                severity=data.get("severity", "blocker"),
                                message=data.get("message"),
                            )
                            extra_policies.append(policy)
                except Exception:  # noqa: S110  # best-effort policy load must not abort
                    pass

    if not has_domain and not args.policies_dir:
        extra_policies.extend(BUILTIN_POLICIES)

    findings = run_deterministic_gates(plan, extra_gates=extra_gates)
    for policy in extra_policies:
        try:
            findings.extend(policy.evaluate(plan))
        except Exception:  # noqa: S110  # policy evaluation must not crash the CLI
            pass

    sev_map = {
        "low": Severity.INFO,
        "medium": Severity.WARNING,
        "high": Severity.BLOCKER,
        "critical": Severity.BLOCKER,
        "blocker": Severity.BLOCKER,
        "warning": Severity.WARNING,
    }
    fail_sev = sev_map.get(args.fail_on_severity, Severity.BLOCKER)
    passed, failures = _gate_verdict(findings, fail_sev)

    if args.output == "json":
        import json as _json

        output = {
            "plan": plan.id,
            "version": plan.version,
            "total_gates": len(findings),
            "failures": len(failures),
            "passed": passed,
            "findings": [
                {
                    "task_id": f.task_id,
                    "severity": f.severity.value,
                    "reason_code": f.reason_code,
                    "message": f.message,
                }
                for f in findings
            ],
        }
        print(_json.dumps(output, indent=2))
    elif args.output == "yaml":
        import yaml as _yaml

        output = {
            "plan": plan.id,
            "version": plan.version,
            "total_gates": len(findings),
            "failures": len(failures),
            "passed": passed,
            "findings": [
                {
                    "task_id": f.task_id,
                    "severity": f.severity.value,
                    "reason_code": f.reason_code,
                    "message": f.message,
                    "suggested_fix": f.suggested_fix,
                }
                for f in findings
            ],
        }
        print(_yaml.dump(output, default_flow_style=False).strip())
    else:
        sev_label = args.fail_on_severity.upper()
        domain_label = args.domain or "default"
        print(f"plancritic check — domain: {domain_label}, enforcement: {args.enforcement.upper()}")
        print()
        if not findings:
            print("All gates PASSED")
        else:
            for f in findings:
                status = (
                    "FAILED" if f.severity in (Severity.BLOCKER, Severity.WARNING) else "PASSED"
                )
                sev = f.severity.value.upper()
                print(f"  Gate: {f.reason_code:<45s} {status:>8s}  ({sev})")
                if f.task_id:
                    print(f"    Task: {f.task_id}")
                print(f"    {f.message}")
                if f.suggested_fix:
                    print(f"    Fix: {f.suggested_fix}")
                print()
        if failures:
            print(
                f"Result: FAILED ({len(failures)} gate violation(s) "
                f"at or above {sev_label} severity)"
            )
        else:
            print("Result: PASSED")

    return 0 if passed else 1


__all__ = ["build_check_parser", "run_check"]
