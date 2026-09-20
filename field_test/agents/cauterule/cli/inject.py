from __future__ import annotations

import click

from cauterule.injection.matcher import match_rules
from cauterule.store.manager import StoreManager


@click.command("inject")
@click.argument("task")
@click.option("--preflight", is_flag=True, help="Dry-run: show rules without injecting.")
def inject(task: str, preflight: bool) -> None:
    """Inject relevant rules into a task context."""
    from cauterule.observe.hits import record_injection_hits

    store = StoreManager()
    rules = store.list_rules(status="active")
    matched = match_rules(task, rules)
    if not matched:
        click.echo("No matching rules found.")
        return
    click.echo(f"Found {len(matched)} matching rule(s):")
    for r in matched:
        click.echo(f'  {r.id}: when "{r.when.trigger}" → {r.do.directive}')
    if preflight:
        return
    record_injection_hits(store, [r.id for r in matched])
    click.echo(f"Recorded hits for {len(matched)} rule(s).")
