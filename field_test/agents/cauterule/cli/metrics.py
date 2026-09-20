from __future__ import annotations

import click

from cauterule.store.health import health_report
from cauterule.store.manager import StoreManager


@click.command("metrics")
@click.option(
    "--coverage",
    is_flag=True,
    help="Show coverage score (40% coverage + 40% precision + 20% non-stale)",
)
@click.option("--by-domain", is_flag=True, help="Show per-domain coverage")
@click.option("--by-class", is_flag=True, help="Show per-class coverage")
@click.option("--rule", "rule_id", default=None, help="Show per-rule outcome summary")
@click.option("--lowest-spec", is_flag=True, help="Show lowest-specificity rules")
@click.option("--store-dir", default="rules", help="Rule store directory")
def metrics(
    coverage: bool,
    by_domain: bool,
    by_class: bool,
    rule_id: str | None,
    lowest_spec: bool,
    store_dir: str,
) -> None:
    """CLI summary: rules, precision, repeat-failure rate, store size."""
    if lowest_spec:
        from cauterule.lifecycle.specificity import BROAD_SPECIFICITY_THRESHOLD, lowest_specificity

        store = StoreManager(base_dir=store_dir)
        rules = store.list_rules()
        table = lowest_specificity(rules, limit=10)
        if not table:
            click.echo("No rules found.")
            return
        click.echo(f"Lowest-specificity rules (broad threshold {BROAD_SPECIFICITY_THRESHOLD:.2f}):")
        for r, spec in table:
            marker = " <-- BROAD" if spec < BROAD_SPECIFICITY_THRESHOLD else ""
            click.echo(f"  {r.id:8s} spec={spec:.2f} {r.status:12s} {r.when.trigger!r}{marker}")
        return
    if rule_id:
        from cauterule.observe.outcomes import rule_outcome_summary, sparkline

        store = StoreManager(base_dir=store_dir)
        summary = rule_outcome_summary(store, rule_id)
        click.echo(f"Rule: {rule_id}")
        click.echo(f"  prevented: {summary['prevented']}")
        click.echo(f"  broke:     {summary['broke']}")
        click.echo(f"  neutral:   {summary['neutral']}")
        click.echo(f"  prevented-rate: {summary['prevented_rate']:.2%}")
        click.echo(f"  last outcome:  {summary['last_outcome']}")
        if summary["last_outcome_at"]:
            click.echo(f"  last outcome at: {summary['last_outcome_at']}")
        trend = summary["trend"]
        if trend:
            int_trend = [int(v) for v in trend]
            click.echo(f"  trend: {sparkline(tuple(int_trend))} ({int_trend})")
        return
    if coverage or by_domain or by_class:
        store = StoreManager(base_dir=store_dir)
        if coverage and not by_domain and not by_class:
            from cauterule.observe.coverage_score import compute_coverage_score

            score = compute_coverage_score(store)
            click.echo(f"Coverage score: {score:.4f}")
            return
        if by_domain:
            from cauterule.observe.domain_coverage import domain_coverage

            cov = domain_coverage(store)
            if not cov:
                click.echo("No domain coverage (no active rules).")
                return
            for domain, val in sorted(cov.items()):
                click.echo(f"{domain}: {val:.2%}")
            return
        if by_class:
            from cauterule.models.trajectory import Trajectory  # noqa: F401

            # Requires trajectories; report empty guidance if none
            click.echo(
                "Class coverage requires trajectories — "
                "use Python API class_coverage(store, trajectories)."
            )
            return

    report = health_report(base_dir=store_dir)
    click.echo(f"Total rules: {report['total_rules']}")
    click.echo(f"By status: {report['by_status']}")
    click.echo(f"Avg confidence: {report['avg_confidence']}")
    click.echo(f"Avg effectiveness: {report['avg_effectiveness']}")
    if report["stale_rules"]:
        click.echo(
            f"Stale rules ({len(report['stale_rules'])}): {', '.join(report['stale_rules'])}"
        )
    # Also show coverage when no specific flag
    if not coverage:
        from cauterule.observe.coverage_score import compute_coverage_score

        try:
            store = StoreManager(base_dir=store_dir)
            score = compute_coverage_score(store)
            click.echo(f"Coverage score: {score:.4f}")
        except Exception:
            pass
