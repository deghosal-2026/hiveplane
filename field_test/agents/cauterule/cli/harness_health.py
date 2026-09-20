"""CLI: cauterule harness-health — benchmark asserts on itself."""

from __future__ import annotations

import click

from cauterule.benchmark.harness import harness_health


@click.command("harness-health")
@click.option("--parsed", type=int, required=True, help="Number of parsed candidates.")
@click.option("--total", type=int, required=True, help="Total trajectories processed.")
@click.option("--candidates", type=int, default=0, help="Total candidates produced.")
@click.option("--trajectories", type=int, default=0, help="Total trajectories.")
@click.option("--threshold", default=0.7, show_default=True, help="Parse rate threshold.")
def harness_health_cli(
    parsed: int, total: int, candidates: int, trajectories: int, threshold: float
) -> None:
    """Check harness health (parse rate, completion ratio)."""
    health = harness_health(
        parsed=parsed,
        total=total,
        candidates=candidates,
        trajectories=trajectories,
        parse_threshold=threshold,
    )

    for check in health.checks:
        status = "PASS" if check.passed else "FAIL"
        click.echo(f"  [{status}] {check.name}: {check.message}")

    if health.passed:
        click.echo("Harness health: PASS")
    else:
        click.echo("Harness health: FAIL — do not trust model results from this run")
        raise click.ClickException("Harness health check failed")  # noqa: TRY003
