from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("show")
@click.argument("rule_id")
@click.option("--hits", is_flag=True, help="Show hit count and last match")
@click.option("--outcomes", is_flag=True, help="Show per-rule outcome summary")
@click.option("--history", "show_history", is_flag=True, help="Show supersession chain")
def show(rule_id: str, hits: bool, outcomes: bool, show_history: bool) -> None:
    """Show full provenance view of a rule."""
    store = StoreManager()
    rule = store.get_rule(rule_id)
    if rule is None:
        click.echo(f"Rule {rule_id!r} not found.")
        return
    if show_history:
        from cauterule.lifecycle.supersede import render_chain

        rules = store.list_rules()
        try:
            from cauterule.lifecycle.supersede import chain

            lineage = chain(rule_id, rules)
        except ValueError as exc:
            click.echo(f"Error: {exc}")
            return
        click.echo(f"Supersession chain for {rule_id}:")
        click.echo(f"  {render_chain(lineage)}")
        for r in lineage:
            click.echo(
                f"    {r.id}: {r.when.trigger[:60]} — {r.status} "
                f"prevented={r.prevented_count} broke={r.broke_count} "
                f"promoted={r.promoted_at[:10]}"
            )
        return
    if outcomes:
        from cauterule.observe.outcomes import rule_outcome_summary, sparkline

        summary = rule_outcome_summary(store, rule_id)
        click.echo(f"Rule: {rule_id}")
        click.echo(f"  prevented: {summary['prevented']}")
        click.echo(f"  broke:     {summary['broke']}")
        click.echo(f"  neutral:   {summary['neutral']}")
        click.echo(f"  prevented-rate: {summary['prevented_rate']:.2%}")
        trend = summary["trend"]
        if trend:
            int_trend = [int(v) for v in trend]
            click.echo(f"  trend: {sparkline(tuple(int_trend))} ({int_trend})")
        return
    click.echo(f"ID: {rule.id}")
    click.echo(f"When: {rule.when.trigger}")
    if rule.when.context:
        click.echo(f"  Context: {', '.join(rule.when.context)}")
    click.echo(f"Do: {rule.do.directive}")
    if rule.do.because:
        click.echo(f"  Because: {rule.do.because}")
    click.echo(f"Confidence: {rule.confidence:.2f}")
    click.echo(f"Status: {rule.status}")
    click.echo(f"Promoted at: {rule.promoted_at}")
    click.echo(f"Hit count: {rule.hit_count}")
    if rule.last_match:
        click.echo(f"Last match: {rule.last_match}")
    elif hits:
        click.echo("Last match: never")
    if rule.specificity is not None:
        click.echo(f"Specificity: {rule.specificity:.2f}")
        if rule.specificity_inputs:
            inputs = rule.specificity_inputs
            click.echo(
                f"  inputs: trigger_tokens={inputs.get('trigger_tokens')} "
                f"matched={inputs.get('matched_count')} "
                f"broken={inputs.get('broken_count')} "
                f"breadth_penalty={inputs.get('breadth_penalty')}"
            )
    elif rule.status == "active":
        from cauterule.lifecycle.specificity import compute_specificity

        spec, inputs = compute_specificity(rule)
        click.echo(
            f"Specificity: {spec:.2f} (computed) trigger_tokens={inputs.get('trigger_tokens')}"
        )
    click.echo(f"Tags: {', '.join(rule.tags) if rule.tags else 'none'}")
    if rule.taxonomy:
        click.echo(f"Taxonomy: {rule.taxonomy}")
    if rule.provenance.replay_evidence:
        ev = rule.provenance.replay_evidence
        click.echo(f"Failures prevented: {len(ev.failures_prevented)}")
        click.echo(f"Successes broken: {len(ev.successes_broken)}")
