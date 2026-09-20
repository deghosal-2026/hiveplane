from __future__ import annotations

import json
from pathlib import Path

import click

from cauterule.export.cli import export_rules, import_rules
from cauterule.store.manager import StoreManager


@click.command("export")
@click.option(
    "--format",
    "fmt",
    default="agents",
    help="Export format: cursorrules, claude, agents, windsurf, aider, markdown, json.",
)
@click.option("--output", "-o", default=None, help="Output file (default: stdout).")
@click.option("--rules-dir", default="rules", help="Rule store directory.")
@click.option("--include-retired", is_flag=True, help="Include retired rules.")
def export(fmt: str, output: str | None, rules_dir: str, include_retired: bool) -> None:
    """Export the rule store to an agent format (e.g. ``--format agents``)."""
    store = StoreManager(base_dir=rules_dir)
    try:
        text = export_rules(store.list_rules(), fmt, include_retired=include_retired)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Exported to {output}")
    else:
        click.echo(text)


@click.command("import")
@click.argument("path")
@click.option("--output", "-o", default=None, help="Candidates JSON output file.")
def import_cmd(path: str, output: str | None) -> None:
    """Import candidate rules from a convention file or chat history."""
    try:
        candidates = import_rules(path)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Imported {len(candidates)} candidate(s) from {path}")
    for cand in candidates:
        click.echo(f"  - {cand.when.trigger} -> {cand.do.directive}")
    out_path = output or f"{Path(path).stem}.candidates.json"
    data = [
        {
            "trigger": c.when.trigger,
            "context": list(c.when.context),
            "directive": c.do.directive,
            "because": c.do.because,
            "confidence": c.confidence,
        }
        for c in candidates
    ]
    Path(out_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    click.echo(f"Candidates written to {out_path}")
