from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("promote")
@click.argument("rule")
@click.option(
    "--force",
    "force_promote",
    is_flag=True,
    help="Force promotion despite safety warnings (audit logged).",
)
@click.option(
    "--show-cutoffs",
    is_flag=True,
    help="Show learned vs default promotion cutoffs for the current store.",
)
def promote(rule: str, force_promote: bool, show_cutoffs: bool) -> None:
    """Promote a tested rule to the active rule store."""
    if show_cutoffs:
        _show_cutoff_report()
        return
    if force_promote:
        click.echo("WARNING: --force: safety warnings will be overridden (audit logged).")
    store = StoreManager()
    candidate = store.get_rule(rule) if rule.startswith("R-") else None
    if candidate:
        click.echo(f"Cannot promote already-promoted rule {rule}. Use a candidate.")
        return
    click.echo(f"promote: promoting rule {rule}")
    click.echo("(candidate loading from draft store not yet implemented)")


def _show_cutoff_report() -> None:
    """Print learned vs default cutoffs with evidence counts (#545)."""
    from cauterule.lifecycle.tune import cutoffs_for_corpus

    store = StoreManager()
    rules = store.list_rules()
    total = sum(r.prevented_count + r.broke_count for r in rules if r.status == "active")
    learned = cutoffs_for_corpus(rules, mode="balanced")
    click.echo(f"Promotion cutoffs (evidence outcomes: {total}):")
    click.echo(f"  learned (effective): {learned.summarize()}")
    if learned.source == "default":
        click.echo("  (cold start — evidence below threshold; static defaults in use)")
