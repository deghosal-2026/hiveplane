from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("leaderboard")
@click.option("--store-dir", default="rules", help="Rule store directory")
@click.option("--top", default=10, help="Top N to show")
def leaderboard(store_dir: str, top: int) -> None:
    """Show failure pattern leaderboard — most prevented, top gaps."""
    from cauterule.observe.leaderboard import get_leaderboard

    store = StoreManager(base_dir=store_dir)
    lb = get_leaderboard(store)
    click.echo("Most prevented:")
    for e in lb.get("most_prevented", [])[:top]:
        click.echo(f"  {e['id']}: {e['count']} failures prevented")
    click.echo("Most broken:")
    for e in lb.get("most_broken", [])[:top]:
        click.echo(f"  {e['id']}: {e['count']} successes broken")
    click.echo("Top gaps (lowest recall):")
    for e in lb.get("top_gaps", [])[:top]:
        click.echo(f"  {e['id']}: recall={e['recall']:.2f}")
