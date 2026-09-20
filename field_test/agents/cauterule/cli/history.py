from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("history")
@click.option("--limit", default=20, help="Number of entries to show.")
def history(limit: int) -> None:
    """Timeline of promotions and retirements."""
    store = StoreManager()
    rules = store.list_rules()
    ordered = sorted(rules, key=lambda r: r.promoted_at, reverse=True)[:limit]
    if not ordered:
        click.echo("No rules found.")
        return
    click.echo("Recent rule activity:")
    for r in ordered:
        click.echo(f"  {r.promoted_at[:10]} {r.id:8s} {r.status:12s} {r.when.trigger[:50]}")
