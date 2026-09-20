from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("frontier")
@click.option("--store-dir", default="rules", help="Rule store directory")
def frontier(store_dir: str) -> None:
    """Recommend next most valuable domain/failure family to learn."""
    from cauterule.observe.coverage_frontier import suggest_next_frontier

    store = StoreManager(base_dir=store_dir)
    suggestion = suggest_next_frontier(store, [])
    click.echo(suggestion)
