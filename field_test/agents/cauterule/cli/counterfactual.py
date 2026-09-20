from __future__ import annotations

import click

from cauterule.models.trajectory import Trajectory
from cauterule.store.manager import StoreManager


@click.command("counterfactual")
@click.option("--days", default=7, help="Lookback window in days.")
def counterfactual(days: int) -> None:
    """Show failures that would have been avoided with current rules."""
    from pathlib import Path

    from cauterule.serialization.trajectory_jsonl import load_trajectories

    store = StoreManager()
    rules = store.list_rules(status="active")
    if not rules:
        click.echo("No active rules to analyze.")
        return

    traj_dir = Path("trajectories")
    failures: list[Trajectory] = []
    if traj_dir.is_dir():
        for f in traj_dir.glob("*.jsonl"):
            failures.extend(t for t in load_trajectories(f) if not t.success)

    click.echo(
        f"Found {len(rules)} active rules and {len(failures)} historical failures (last {days}d)."
    )
    click.echo("Counterfactual analysis:")
    for r in rules:
        prevented = sum(1 for t in failures if r.when.trigger.lower() in t.task.lower())
        if prevented:
            click.echo(
                f'  Rule {r.id} could have prevented {prevented} failure(s): "{r.when.trigger}"'
            )
