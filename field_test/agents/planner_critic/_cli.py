"""CLI entry point — ``plancritic``.

M1 shipped only ``--version``. M2 adds the first functional subcommands
(``migrate``, and ``providers`` from the M2 registry work); the full CLI
(plan/critique/plans/escalate/...) is an M6 concern. Subcommands live in
:mod:`planner_critic.cli` and each exposes ``build_*_parser`` + ``run_*`` so
they are self-describing and testable in isolation.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from . import __version__
from .cli import (  # type: ignore[attr-defined, unused-ignore]
    build_check_parser,
    build_corpus_parser,
    build_critique_parser,
    build_demo_parser,
    build_diagnose_parser,
    build_domains_parser,
    build_escalate_parser,
    build_eval_parser,
    build_field_test_parser,
    build_findings_parser,
    build_gates_parser,
    build_init_parser,
    build_lessons_parser,
    build_migrate_parser,
    build_plan_parser,
    build_plans_parser,
    build_policy_parser,
    build_providers_parser,
    build_quickstart_parser,
    build_quota_parser,
    build_replay_parser,
    build_templates_parser,
    run_check,
    run_corpus,
    run_critique,
    run_demo,
    run_diagnose,
    run_domains,
    run_escalate,
    run_eval,
    run_field_test,
    run_findings,
    run_gates_canary,
    run_init,
    run_lessons,
    run_migrate,
    run_plan,
    run_plans,
    run_policy,
    run_providers,
    run_quickstart,
    run_quota,
    run_replay,
    run_templates,
)

SubcommandRunner = Callable[[list[str]], int]

_SUBCOMMANDS: dict[str, tuple[argparse.ArgumentParser, SubcommandRunner]] = {
    "check": (build_check_parser(), run_check),
    "corpus": (build_corpus_parser(), run_corpus),
    "critique": (build_critique_parser(), run_critique),
    "demo": (build_demo_parser(), run_demo),
    "diagnose": (build_diagnose_parser(), run_diagnose),
    "domains": (build_domains_parser(), run_domains),
    "escalate": (build_escalate_parser(), run_escalate),
    "eval": (build_eval_parser(), run_eval),
    "field-test": (build_field_test_parser(), run_field_test),
    "findings": (build_findings_parser(), run_findings),
    "gates": (build_gates_parser(), run_gates_canary),
    "init": (build_init_parser(), run_init),
    "lessons": (build_lessons_parser(), run_lessons),
    "migrate": (build_migrate_parser(), run_migrate),
    "plan": (build_plan_parser(), run_plan),
    "plans": (build_plans_parser(), run_plans),
    "policy": (build_policy_parser(), run_policy),
    "providers": (build_providers_parser(), run_providers),
    "quota": (build_quota_parser(), run_quota),
    "quickstart": (build_quickstart_parser(), run_quickstart),
    "replay": (build_replay_parser(), run_replay),
    "templates": (build_templates_parser(), run_templates),
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser with its subcommands.

    Returns:
        The root parser; subcommand parsers are attached as children.
    """
    parser = argparse.ArgumentParser(prog="plancritic", description="PlannerCritic Engine CLI")
    parser.add_argument("--version", action="version", version=f"plancritic {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, (sub_parser, _) in _SUBCOMMANDS.items():
        subparsers.add_parser(name, parents=[sub_parser], help=sub_parser.description)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point (console script target).

    Args:
        argv: Argument list; None means ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    raw = list(argv) if argv is not None else sys.argv[1:]
    rest = raw[raw.index(args.command) + 1 :]
    return _SUBCOMMANDS[args.command][1](rest)


if __name__ == "__main__":  # pragma: no cover - console-script path
    raise SystemExit(main())
