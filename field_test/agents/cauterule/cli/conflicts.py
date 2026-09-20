from __future__ import annotations

import click

from cauterule.conflict import detect_contradictions, detect_duplicates, detect_overlaps
from cauterule.serialization.rule_yaml import load_rules_from_dir


def _pair(report: object) -> frozenset[str]:
    return frozenset(report.rules)  # type: ignore[attr-defined]


@click.command("conflicts")
def conflicts() -> None:
    """List detected conflicts between active rules."""
    rules = load_rules_from_dir("rules")
    if not rules:
        click.echo("No rules found in store.")
        return
    contradictions = detect_contradictions(rules)
    overlaps = detect_overlaps(rules)
    duplicates = detect_duplicates(rules)
    if not contradictions and not overlaps and not duplicates:
        click.echo("No conflicts detected.")
        return
    # Prefer the stronger signal (duplicate > contradiction > overlap) per pair
    # so a pair is not reported twice (#code-review).
    duplicate_pairs = {_pair(d) for d in duplicates}
    contradiction_pairs = {_pair(c) for c in contradictions}
    for c in contradictions:
        click.echo(f"CONTRADICTION: {c.rules[0]} vs {c.rules[1]} — {c.resolution}")
    for d in duplicates:
        click.echo(f"DUPLICATE: {d.rules[0]} vs {d.rules[1]} — {d.resolution}")
    for o in overlaps:
        if _pair(o) in duplicate_pairs or _pair(o) in contradiction_pairs:
            continue
        click.echo(f"OVERLAP: {o.rules[0]} vs {o.rules[1]} — {o.resolution}")
