from __future__ import annotations

import click

from cauterule.models.rule import StandingRule
from cauterule.store.health import health_report


@click.command("health")
@click.option("--by-taxonomy", is_flag=True, help="Breakdown by failure taxonomy.")
def health(by_taxonomy: bool) -> None:
    """Rule store health report."""
    report = health_report()
    click.echo(f"Total rules: {report['total_rules']}")
    click.echo(f"By status: {report['by_status']}")
    click.echo(f"Avg confidence: {report['avg_confidence']}")
    click.echo(f"Avg effectiveness: {report['avg_effectiveness']}")
    click.echo(f"Conflict count: {report['conflict_count']}")
    if report["stale_rules"]:
        click.echo(f"Stale rules: {', '.join(report['stale_rules'])}")
    if by_taxonomy:
        from cauterule.store.manager import StoreManager

        store = StoreManager()
        groups: dict[str, list[StandingRule]] = {}
        for rule in store.list_rules():
            groups.setdefault(rule.taxonomy or "unlabeled", []).append(rule)
        click.echo("By taxonomy:")
        for taxonomy, members in sorted(groups.items()):
            prevented = sum(r.prevented_count for r in members)
            broke = sum(r.broke_count for r in members)
            total = prevented + broke + sum(r.neutral_count for r in members)
            precision = (prevented / total) if total else 0.0
            click.echo(
                f"  {taxonomy}: {len(members)} rules, "
                f"precision={precision:.2%} (prevented={prevented} broke={broke})"
            )
