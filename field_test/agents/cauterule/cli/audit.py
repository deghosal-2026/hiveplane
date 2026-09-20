from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("audit")
@click.argument("rule_id", required=False)
@click.option("--apply", "apply_", is_flag=True, help="Apply auto-retirement (mutates).")
@click.option("--yes", is_flag=True, help="Apply without per-rule confirmation.")
@click.option("--stale-days", type=int, default=None, help="Retirement policy override.")
@click.option("--min-evidence", type=int, default=None, help="Retirement policy override.")
def audit(
    rule_id: str | None,
    apply_: bool,
    yes: bool,
    stale_days: int | None,
    min_evidence: int | None,
) -> None:
    """Show rule provenance, or (no rule) run the retirement audit pass."""
    if rule_id is not None:
        _show_provenance(rule_id)
        return
    _run_retirement_audit(apply_=apply_, yes=yes, stale_days=stale_days, min_evidence=min_evidence)


def _show_provenance(rule_id: str) -> None:
    store = StoreManager()
    rule = store.get_rule(rule_id)
    if rule is None:
        click.echo(f"Rule {rule_id!r} not found.")
        return
    prov = rule.provenance
    click.echo(f"Rule: {rule.id}")
    click.echo(f"  Status: {rule.status}")
    click.echo(f"  Source trajectory: {prov.source_trajectory}")
    click.echo(f"  Extracted by: {prov.extracted_by}")
    click.echo(f"  Extract timestamp: {prov.extract_timestamp}")
    click.echo(f"  Extraction pass: {prov.extraction_pass}")
    click.echo(f"  Promotion commit: {prov.promotion_commit}")
    click.echo(f"  Promotion mode: {prov.promotion_mode}")
    if prov.draft_tournament_rank is not None:
        click.echo(f"  Tournament rank: {prov.draft_tournament_rank}")


def _run_retirement_audit(
    *,
    apply_: bool,
    yes: bool,
    stale_days: int | None,
    min_evidence: int | None,
) -> None:
    """Print retirement candidates; apply them when --apply is set (#543)."""
    from cauterule.lifecycle.retire import (
        RetirementPolicy,
        apply_candidates,
        evaluate,
    )

    store = StoreManager()
    if stale_days is not None or min_evidence is not None:
        policy = RetirementPolicy(
            stale_days=stale_days if stale_days is not None else RetirementPolicy().stale_days,
            min_evidence=min_evidence
            if min_evidence is not None
            else RetirementPolicy().min_evidence,
        )
    else:
        policy = RetirementPolicy()

    candidates = evaluate(store.list_rules(), policy)
    if not candidates:
        click.echo("No retirement candidates.")
        return

    click.echo(f"Retirement candidates ({len(candidates)}):")
    for c in candidates:
        click.echo(f"  {c.render()}")

    if apply_:
        if not yes:
            click.echo("Confirm retirement of all candidates above? (--yes to skip)")
            return
        retired = apply_candidates(store, candidates)
        click.echo(f"Retired {len(retired)}: {', '.join(retired)}")
    else:
        click.echo("(Dry run — pass --apply to mutate.)")
