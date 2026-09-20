from __future__ import annotations

import click

from cauterule.injection.matcher import match_rules
from cauterule.store.manager import StoreManager


@click.command("search")
@click.argument("query")
def search(query: str) -> None:
    """Full-text search across rules."""
    store = StoreManager()
    rules = store.list_rules()
    matched = match_rules(query, rules)
    if not matched:
        click.echo(f"No rules match query {query!r}.")
        return
    click.echo(f"Found {len(matched)} matching rule(s):")
    for r in matched:
        click.echo(f'  {r.id}: "{r.when.trigger}" → {r.do.directive}')
