"""CLI subcommand package.

M1 shipped the bare ``plancritic --version`` entry point. M2 adds the first
functional subcommands — ``migrate`` (store schema) and ``providers``
(registry) — while the full CLI surface (plan/critique/plans/escalate/...) is
an M6 concern. Each module exposes a ``run(args) -> int`` function registered
in :mod:`planner_critic._cli`; the argument parser stays in the subcommand
module so a command is self-describing and testable in isolation.
"""

from __future__ import annotations

from .check import build_check_parser, run_check
from .corpus import build_corpus_parser, run_corpus
from .critique import build_critique_parser, run_critique
from .demo import build_demo_parser, run_demo
from .diagnose import build_diagnose_parser, run_diagnose
from .domains import build_domains_parser, run_domains
from .escalate import build_escalate_parser, run_escalate
from .eval import build_eval_parser, run_eval
from .field_test import build_field_test_parser, run_field_test
from .findings import build_findings_parser, run_findings
from .gates_canary import build_gates_parser, run_gates_canary
from .init import build_init_parser, run_init
from .lessons import build_lessons_parser, run_lessons
from .migrate import build_migrate_parser, run_migrate
from .plan import build_plan_parser, run_plan
from .plans import build_plans_parser, run_plans
from .policy import build_policy_parser, run_policy
from .providers import build_providers_parser, run_providers
from .quickstart import build_quickstart_parser, run_quickstart
from .quota import build_quota_parser, run_quota
from .replay import build_replay_parser, run_replay
from .templates import build_templates_parser, run_templates

__all__ = [
    "build_check_parser",
    "build_corpus_parser",
    "build_critique_parser",
    "build_demo_parser",
    "build_diagnose_parser",
    "build_domains_parser",
    "build_escalate_parser",
    "build_eval_parser",
    "build_field_test_parser",
    "build_findings_parser",
    "build_gates_parser",
    "build_init_parser",
    "build_lessons_parser",
    "build_migrate_parser",
    "build_plan_parser",
    "build_plans_parser",
    "build_policy_parser",
    "build_providers_parser",
    "build_quickstart_parser",
    "build_quota_parser",
    "build_replay_parser",
    "build_templates_parser",
    "run_check",
    "run_corpus",
    "run_critique",
    "run_demo",
    "run_diagnose",
    "run_domains",
    "run_escalate",
    "run_eval",
    "run_field_test",
    "run_findings",
    "run_gates_canary",
    "run_init",
    "run_lessons",
    "run_migrate",
    "run_plan",
    "run_plans",
    "run_policy",
    "run_providers",
    "run_quickstart",
    "run_quota",
    "run_replay",
    "run_templates",
]
