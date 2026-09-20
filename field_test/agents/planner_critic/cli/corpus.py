"""``plancritic corpus`` — SWE-bench security corpus management (M5, #123).

Subcommands:
  ``list``        — list all instances with metadata
  ``show``        — show a single instance in detail
  ``manifest``    — show corpus manifest
  ``load``        — load and validate all instances (checksums optional)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..corpus import list_instances, load_all_instances, load_corpus_manifest, load_instance

_DEFAULT_CORPUS = str(
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "field-test"
    / "corpus"
    / "swebench-security"
)


def build_corpus_parser() -> argparse.ArgumentParser:
    """Build the ``plancritic corpus`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic corpus",
        description="SWE-bench security corpus management",
        add_help=False,
    )
    parser.add_argument(
        "--corpus-dir",
        default=_DEFAULT_CORPUS,
        help="Path to the corpus directory (default: bundled swebench-security)",
    )

    sub = parser.add_subparsers(dest="action", metavar="ACTION", required=True)

    sub.add_parser("list", help="List all instances with CWE bucket, class, signal")

    show_parser = sub.add_parser("show", help="Show a single instance in detail")
    show_parser.add_argument("instance_id", help="Instance identifier (e.g. CWE-079-001)")

    sub.add_parser("manifest", help="Show corpus manifest metadata")

    load_parser = sub.add_parser(
        "load", help="Load and validate all instances (with optional checksum verification)"
    )
    load_parser.add_argument(
        "--verify-checksums",
        action="store_true",
        help="Verify SHA-256 checksums against manifest (load subcommand)",
    )

    return parser


def run_corpus(argv: list[str]) -> int:
    parser = build_corpus_parser()
    args = parser.parse_args(argv)
    corpus_dir = args.corpus_dir

    if args.action == "list":
        rows = list_instances(corpus_dir)
        if not rows:
            print("(no instances found)")
            return 0
        header = f"{'ID':<20} {'CWE':<12} {'Bucket':<18} {'Class':<28} {'Signal':<18}"
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r['instance_id']:<20} {r['cwe']:<12} {r['cwe_bucket']:<18} "
                f"{r['vulnerability_class']:<28} {r['expected_critic_signal'] or '—':<18}"
            )
        print(f"\n{len(rows)} instances")
        return 0

    if args.action == "show":
        inst = load_instance(corpus_dir, args.instance_id)
        if inst is None:
            print(f"instance not found: {args.instance_id}")
            return 1
        print(f"ID:          {inst.instance_id}")
        print(f"CWE:         {inst.cwe} ({inst.cwe_bucket.value})")
        print(f"Class:       {inst.vulnerability_class}")
        print(f"License:     {inst.license}")
        signal = inst.expected_critic_signal.value if inst.expected_critic_signal else "—"
        print(f"Signal:      {signal}")
        codes = ", ".join(inst.expected_reason_codes) if inst.expected_reason_codes else "—"
        print(f"Reason codes: {codes}")
        print(f"\nIssue:\n{inst.issue_description}")
        print(f"\nGoal:\n{inst.goal_text}")
        print(f"\nGround truth:\n{inst.ground_truth_summary}")
        return 0

    if args.action == "manifest":
        manifest = load_corpus_manifest(corpus_dir)
        if manifest is None:
            print("no manifest found")
            return 1
        print(f"Corpus:      {manifest.name}")
        print(f"Version:     {manifest.version}")
        print(f"Created:     {manifest.created}")
        print(f"Instances:   {manifest.instance_count}")
        print(f"Description: {manifest.description}")
        if manifest.cwe_counts:
            print("\nCWE breakdown:")
            for bucket, count in sorted(manifest.cwe_counts.items()):
                print(f"  {bucket.value}: {count}")
        return 0

    if args.action == "load":
        instances = load_all_instances(corpus_dir, verify_checksums=args.verify_checksums)
        if not instances:
            print("(no instances loaded)")
            return 0
        print(f"Loaded {len(instances)} instance(s)")
        for inst in instances:
            print(f"  {inst.instance_id} — {inst.cwe} ({inst.cwe_bucket.value})")
        if args.verify_checksums:
            manifest = load_corpus_manifest(corpus_dir)
            if manifest and manifest.instance_count != len(instances):
                print(
                    f"WARNING: loaded {len(instances)} but manifest lists {manifest.instance_count}"
                )
        return 0

    parser.print_help()
    return 1


__all__ = [
    "build_corpus_parser",
    "run_corpus",
]
