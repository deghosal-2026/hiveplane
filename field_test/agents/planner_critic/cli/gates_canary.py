"""``plancritic gates canary`` — deterministic gate health assertions (#278).

Each gate class gets a ``(good_plan, bad_plan)`` fixture pair. The canary
asserts every gate still fires on its known-bad plan and stays silent on its
known-good plan. Runs as:

* ``plancritic gates canary --check`` — exit 1 if any gate fails (CI gate).
* ``plancritic gates canary --report`` — machine-readable JSON output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..gates import run_deterministic_gates
from ..gates.base import BaseGate
from ..schema.plan import PlanVersion
from ..types import HeuristicFamily, Severity

CANARY_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "canary"


class CanaryResult:
    """Result of a single canary check for one gate class."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.healthy = True
        self.expected_blocker: str = ""
        self.actual_findings: list[str] = []
        self.error: str = ""


def _load_canary_fixture(name: str, variant: str) -> PlanVersion:
    """Load a canary fixture from ``tests/canary/``.

    Args:
        name: Gate name (e.g. ``ordering``).
        variant: ``good`` or ``bad``.

    Returns:
        The loaded plan.

    Raises:
        FileNotFoundError: if the fixture does not exist.
    """
    path = CANARY_FIXTURES_DIR / name / f"{variant}.json"
    if not path.is_file():
        raise FileNotFoundError(f"canary fixture not found: {path}")
    return PlanVersion.from_dict(json.loads(path.read_text()))


def _check_single_gate(
    gate_idx: int,
    gate_name: str,
    gate: BaseGate,
    good_plan: PlanVersion,
    bad_plan: PlanVersion,
    expected_blocker: str,
) -> CanaryResult:
    """Run a single gate's canary: must pass good, block bad.

    Args:
        gate_idx: Index in the GATES list.
        gate_name: Human-readable gate name.
        gate: The gate instance.
        good_plan: Plan the gate should approve.
        bad_plan: Plan the gate should block.
        expected_blocker: Expected reason code on the bad plan.

    Returns:
        The canary result.
    """
    result = CanaryResult(gate_name)
    result.expected_blocker = expected_blocker

    try:
        good_findings = list(gate.run(good_plan))
        if good_findings:
            result.error = (
                f"gate {gate_name} produced {len(good_findings)} finding(s) "
                f"on the GOOD plan: {[str(f) for f in good_findings]}"
            )
            result.healthy = False
            return result
    except Exception as exc:
        result.error = f"gate {gate_name} raised on GOOD plan: {exc}"
        result.healthy = False
        return result

    try:
        bad_findings = list(gate.run(bad_plan))
        matching_findings = [f for f in bad_findings if f.reason_code == expected_blocker]
        if not matching_findings:
            result.error = (
                f"gate {gate_name} expected reason_code={expected_blocker} "
                f"but produced codes: {[f'{f.reason_code}(sev={f.severity.name})' for f in bad_findings]}"
            )
            result.healthy = False
            return result
        result.actual_findings = [f.reason_code for f in matching_findings]
    except Exception as exc:
        result.error = f"gate {gate_name} raised on BAD plan: {exc}"
        result.healthy = False
        return result

    return result


def _get_canary_configs() -> list[dict[str, Any]]:
    """Return the list of canary configurations for each gate.

    Each config has ``gate``, ``name``, and ``expected_blocker``.
    """
    from ..gates import GATES
    
    gate_map: dict[str, tuple[str, str]] = {
        "schema_valid": ("schema_valid", "plan_schema_invalid"),
        "dep_cycles": ("dep_cycles", "dependency_cycle"),
        "ordering": ("ordering", "unsafe_ordering"),
        "verification": ("verification", "missing_verification"),
        "verification_ordering": ("verification_ordering", "verification_after_consumer"),
        "rollback": ("rollback", "missing_rollback"),
        "rollback_credible": ("rollback_credible", "rollback_unreachable"),
        "preconditions": ("preconditions", "unverified_precondition"),
        "parallel_safety": ("parallel_safety", "unsafe_parallelization"),
        "requirement_trace": ("requirement_trace", "step_not_traced_to_criterion"),
    }

    configs = []
    for gate in GATES:
        mod_name = type(gate).__module__.split(".")[-1]
        if mod_name in gate_map:
            name, blocker = gate_map[mod_name]
            configs.append({
                "gate": gate,
                "name": name,
                "expected_blocker": blocker,
            })
        else:
            configs.append({
                "gate": gate,
                "name": mod_name,
                "expected_blocker": "BLOCKER",
            })
    return configs


def run_canary_check(canary_dir: str | None = None) -> list[CanaryResult]:
    """Run all gate canary checks.

    Args:
        canary_dir: Override path to canary fixtures directory.

    Returns:
        List of canary results, one per gate.
    """
    global CANARY_FIXTURES_DIR
    if canary_dir is not None:
        CANARY_FIXTURES_DIR = Path(canary_dir)

    configs = _get_canary_configs()
    results: list[CanaryResult] = []

    for cfg in configs:
        gate = cfg["gate"]
        name = cfg["name"]
        expected = cfg["expected_blocker"]

        try:
            good = _load_canary_fixture(name, "good")
            bad = _load_canary_fixture(name, "bad")
        except FileNotFoundError as exc:
            result = CanaryResult(name)
            result.error = str(exc)
            result.healthy = False
            results.append(result)
            continue

        result = _check_single_gate(0, name, gate, good, bad, expected)
        results.append(result)

    return results


def build_gates_parser() -> argparse.ArgumentParser:
    """Build the ``gates canary`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic gates canary",
        description="Deterministic gate health check (exit 1 on any regression)",
        add_help=False,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run all canaries; exit 1 if any gate fails (CI gate)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--canary-dir",
        default=None,
        help="Override canary fixtures directory",
    )
    return parser


def run_gates_canary(argv: list[str]) -> int:
    """Run the gate canary CLI subcommand.

    Args:
        argv: CLI arguments (after 'gates canary').

    Returns:
        Exit code: 0 if all gates pass, 1 if any gate fails.
    """
    import argparse

    args = build_gates_parser().parse_args(argv)

    results = run_canary_check(canary_dir=args.canary_dir)

    if args.report:
        report = {
            "version": "v0.2.3",
            "total": len(results),
            "passed": sum(1 for r in results if r.healthy),
            "failed": sum(1 for r in results if not r.healthy),
            "results": [
                {
                    "gate": r.name,
                    "healthy": r.healthy,
                    "expected_blocker": r.expected_blocker,
                    "actual_findings": r.actual_findings,
                    "error": r.error,
                }
                for r in results
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for r in results:
            status = "✅" if r.healthy else "❌"
            print(f"  {status} {r.name}: expected={r.expected_blocker}")
            if r.error:
                print(f"     error: {r.error}")

    passed = all(r.healthy for r in results)
    if args.check:
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    sys.exit(run_gates_canary(sys.argv[1:]))


__all__ = ["build_gates_parser", "run_gates_canary", "run_canary_check"]