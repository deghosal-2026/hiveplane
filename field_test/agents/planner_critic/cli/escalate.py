"""``plancritic escalate`` — list, approve, deny, and patch escalations (F-31, F-34).

Drives the escalation manager over a SQLite plan store. A reviewer can list
open escalations, and approve/deny them; ``approve --patch <file>`` loads a
reviewer-supplied PlanVersion JSON, stores it as the next revision, and
re-submits it to the critic (deterministic gates in the hermetic CLI path)
before recording the approval. The CLI never calls a paid LLM: re-critique
after a patch uses the free, injection-immune deterministic gates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal, cast

from ..escalation import EscalationManager, build_escalation_manager
from ..roles import CriticRole
from ..schema.plan import PlanVersion
from ..store.base import StoreUnavailable
from ..store.sqlite import SQLiteStore
from ..types import Escalation, Finding

DEFAULT_DB_PATH = ".plancritic/plans.db"

EscalationStatus = str  # "open" | "approved" | "denied" (argparse choices)


class _DeterministicCritic(CriticRole):
    """CriticRole that only surfaces deterministic-gate findings.

    Used for the post-patch re-critique in the hermetic CLI path: the gates
    are free, deterministic, and injection-immune — a patched plan that still
    trips a gate blocker is refused (F-73) without spending an LLM call.
    """

    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
        """Return findings unchanged: the gates already spoke."""
        return list(findings)


def build_escalate_parser() -> argparse.ArgumentParser:
    """Build the ``escalate`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic escalate",
        description="Manage plan escalations (F-31, F-34)",
        add_help=False,
    )
    parser.add_argument(
        "--store", default=DEFAULT_DB_PATH, help="SQLite store path (default: %(default)s)"
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    lst = sub.add_parser("list", help="List escalations")
    lst.add_argument("--status", choices=["open", "approved", "denied"], default=None)

    approve = sub.add_parser("approve", help="Approve an escalation")
    approve.add_argument("escalation_id", help="Escalation id")
    approve.add_argument("--patch", default=None, help="PlanVersion JSON to store and re-critique")
    approve.add_argument("--note", default="", help="Resolution note")
    approve.add_argument(
        "--principal",
        default=None,
        help="Approving principal (required when approving_authority is set)",
    )

    deny = sub.add_parser("deny", help="Deny an escalation")
    deny.add_argument("escalation_id", help="Escalation id")
    deny.add_argument("--note", default="", help="Resolution note")
    deny.add_argument(
        "--principal",
        default=None,
        help="Denying principal (required when approving_authority is set)",
    )
    return parser


def run_escalate(argv: list[str]) -> int:
    """Run an escalate subcommand; return a process exit code.

    Args:
        argv: Arguments for the ``escalate`` subcommand.

    Returns:
        0 on success, 1 on a store/escalation failure.
    """
    args = build_escalate_parser().parse_args(argv)
    try:
        store = SQLiteStore(args.store)
        try:
            if args.action in ("approve", "deny"):
                manager = build_escalation_manager(store, escalation_id=args.escalation_id)
            else:
                manager = EscalationManager(store)
            if args.action == "list":
                return _run_list(manager, args.status)
            if args.action == "approve":
                return _run_approve(manager, args)
            if args.action == "deny":
                return _run_deny(manager, args)
            print("usage: plancritic escalate list|approve|deny ...")
            return 1
        finally:
            store.close()
    except (StoreUnavailable, ValueError, json.JSONDecodeError, OSError) as err:
        print(f"escalate failed: {err}")
        return 1


def _run_list(manager: EscalationManager, status: str | None) -> int:
    """Render the escalation listing; always succeeds."""
    typed_status = cast("Literal['open', 'approved', 'denied'] | None", status)
    escalations = manager.list_escalations(status=typed_status)
    if not escalations:
        print("no escalations" if status is None else f"no {status} escalations")
        return 0
    for esc in escalations:
        _print_escalation(esc)
    return 0


def _run_approve(manager: EscalationManager, args: argparse.Namespace) -> int:
    """Approve an escalation, optionally patching the plan first."""
    if args.patch:
        patch = PlanVersion.from_dict(json.loads(Path(args.patch).read_text()))
        manager.patch_and_recritique(
            plan_id=patch.id,
            patch=patch,
            critic=_DeterministicCritic(),
        )
    principal = args.principal or None
    resolved = manager.resolve(args.escalation_id, "approved", note=args.note, principal=principal)
    print(f"escalation {resolved.id} approved")
    return 0


def _run_deny(manager: EscalationManager, args: argparse.Namespace) -> int:
    """Deny an escalation."""
    principal = args.principal or None
    resolved = manager.resolve(args.escalation_id, "denied", note=args.note, principal=principal)
    print(f"escalation {resolved.id} denied")
    return 0


def _print_escalation(esc: Escalation) -> None:
    """Render one escalation for ``escalate list``."""
    state = esc.status
    print(f"[{state}] {esc.id} plan={esc.plan_id} v={esc.version}")
    print(f"    question: {esc.question}")
    if esc.resolution:
        print(f"    resolution: {esc.resolution}")


__all__ = ["build_escalate_parser", "run_escalate"]
