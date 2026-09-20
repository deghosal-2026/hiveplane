from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..domains.base import DomainPack, find_domain_packs, load_domain_pack_from_manifest
from ..gates import run_deterministic_gates
from ..schema.plan import PlanVersion


def build_domains_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plancritic domains",
        description="Manage domain packs (M7)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="domains_command", required=True)

    sub.add_parser("list", help="List installed domain packs")
    show = sub.add_parser("show", help="Show domain pack details")
    show.add_argument("name", help="Pack name")
    add = sub.add_parser("add", help="Register a domain pack")
    add.add_argument("path", help="Path to a domain-pack.yaml file")
    add.add_argument("--name", default=None, help="Override pack name (default: from manifest)")
    test = sub.add_parser("test", help="Dry-run domain gates against a plan")
    test.add_argument("name", help="Pack name or manifest path")
    test.add_argument("plan_file", help="Path to a PlanVersion JSON file")
    return parser


def run_domains(argv: list[str]) -> int:
    args = build_domains_parser().parse_args(argv)

    if args.domains_command == "list":
        packs = find_domain_packs()
        if not packs:
            print("No domain packs installed.")
            return 0
        print(f"Installed domain packs ({len(packs)}):")
        for name, pack in sorted(packs.items()):
            gate_names = [g.name for g in pack.gate_evaluators]
            precond_count = len(pack.precondition_catalog)
            print(f"  {name}")
            print(f"    Gates: {', '.join(gate_names) or 'none'}")
            print(f"    Preconditions: {precond_count}")
            print(f"    Has critic prompt: {'yes' if pack.critic_prompt_template else 'no'}")
        return 0

    if args.domains_command == "show":
        packs = find_domain_packs()
        if args.name not in packs:
            print(f"error: domain pack {args.name!r} not found", file=sys.stderr)
            return 1
        pack = packs[args.name]
        print(f"Domain pack: {pack.name}")
        gate_names = [g.name for g in pack.gate_evaluators]
        precond_count = len(pack.precondition_catalog)
        print(f"  Gates ({len(gate_names)}): {', '.join(gate_names) or 'none'}")
        if pack.precondition_catalog:
            print(f"  Preconditions ({precond_count}):")
            for key, desc in pack.precondition_catalog.items():
                print(f"    {key}: {desc}")
        else:
            print("  Preconditions: none")
        print(f"  Critic prompt: {'yes' if pack.critic_prompt_template else 'no'}")
        pack_config = getattr(pack, "pack_config", {})
        if pack_config:
            print(f"  Config: {json.dumps(pack_config, indent=4)}")
        return 0

    if args.domains_command == "add":
        try:
            pack = load_domain_pack_from_manifest(args.path)
            print(f"Registered domain pack: {pack.name} from {args.path}")
            if args.name:
                print(f"  (requested name: {args.name})")
        except Exception as err:
            print(f"error: failed to load domain pack: {err}", file=sys.stderr)
            return 1
        return 0

    if args.domains_command == "test":
        try:
            domain_path = Path(args.name)
            loaded: DomainPack | None = None
            if domain_path.exists():
                loaded = load_domain_pack_from_manifest(domain_path)
            else:
                packs = find_domain_packs()
                loaded = packs.get(args.name)
            if loaded is None:
                print(f"error: domain pack {args.name!r} not found", file=sys.stderr)
                return 1
            pack = loaded
        except Exception as err:
            print(f"error: failed to load domain pack: {err}", file=sys.stderr)
            return 1

        try:
            data = json.loads(Path(args.plan_file).read_text())
            plan = PlanVersion.model_validate(data)
        except Exception as err:
            print(f"error: failed to load plan: {err}", file=sys.stderr)
            return 1

        findings = run_deterministic_gates(plan, extra_gates=pack.gate_evaluators)
        if not findings:
            print(f"Domain pack {pack.name}: all gates PASSED")
        else:
            for f in findings:
                print(f"  [{f.severity.value}] {f.reason_code}: {f.message} (task={f.task_id})")
        return 1 if findings else 0

    return 0


__all__ = ["build_domains_parser", "run_domains"]
