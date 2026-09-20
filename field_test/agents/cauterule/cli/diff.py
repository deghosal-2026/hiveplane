from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("diff")
@click.argument("rule_id")
def diff(rule_id: str) -> None:
    """Show changes between rule versions."""
    store = StoreManager()
    rule = store.get_rule(rule_id)
    if rule is None:
        click.echo(f"Rule {rule_id!r} not found.")
        return
    click.echo(f"Rule {rule.id} — current version (versioning not implemented)")
    click.echo(f"  Status: {rule.status}")
    click.echo(f"  Promoted at: {rule.promoted_at}")
    click.echo(f"  Hit count: {rule.hit_count}")
