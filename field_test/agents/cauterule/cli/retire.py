from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("retire")
@click.argument("rule_id")
@click.option("--reason", default="", help="Reason for retirement.")
def retire(rule_id: str, reason: str) -> None:
    """Retire a rule with an optional reason."""
    store = StoreManager()
    try:
        store.retire_rule(rule_id, reason)
        click.echo(f"Rule {rule_id} retired.")
    except ValueError as e:
        raise click.ClickException(str(e)) from e
