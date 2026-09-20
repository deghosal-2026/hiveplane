from __future__ import annotations

import click

from cauterule.injection.explainer import explain_rule
from cauterule.store.manager import StoreManager


@click.command("explain")
@click.argument("rule_id")
def explain(rule_id: str) -> None:
    """LLM explains why a rule fires."""
    store = StoreManager()
    rule = store.get_rule(rule_id)
    if rule is None:
        click.echo(f"Rule {rule_id!r} not found.")
        return
    click.echo(explain_rule(rule))
