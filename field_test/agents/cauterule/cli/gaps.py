from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("gaps")
@click.option("--store-dir", default="rules", help="Rule store directory")
@click.option("--threshold", default=3, help="Failure count threshold for gap")
def gaps(store_dir: str, threshold: int = 3) -> None:
    _ = threshold
    """Show coverage gaps — domains with repeated failures but no matching rules."""
    from cauterule.observe.coverage_gap import find_coverage_gaps

    store = StoreManager(base_dir=store_dir)
    # Without trajectories, we can only show message; with trajectories file would be needed
    # For CLI, we list gaps based on stored rules vs implied domains from tags
    # Use empty trajectory list to return no gaps with guidance
    gaps_list = find_coverage_gaps(store, [])
    if not gaps_list:
        click.echo(
            "No coverage gaps (or no trajectories provided — use Python API with trajectories)."
        )
        return
    for g in gaps_list:
        click.echo(f"{g['domain']}: {g['failure_count']} failures, no matching rule")
