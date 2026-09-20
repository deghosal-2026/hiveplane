from __future__ import annotations

import click

from cauterule.serialization.rule_yaml import last_load_errors
from cauterule.store.manager import StoreManager


@click.command("list")
@click.option("--status", help="Filter by status (active/retired/draft).")
@click.option("--tag", help="Filter by tag.")
@click.option("--taxonomy", "taxonomy_filter", default=None, help="Filter by taxonomy category.")
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["id", "spec"]),
    default="id",
    help="Sort column.",
)
def list_rules(
    status: str | None, tag: str | None, taxonomy_filter: str | None, sort_by: str
) -> None:
    """List rules as a table with id, trigger, status, hits, tags, spec."""
    store = StoreManager()
    rules = store.list_rules(status=status)
    if last_load_errors:
        click.echo(f"Warning: {len(last_load_errors)} rule file(s) skipped:")
        for path, error in last_load_errors:
            click.echo(f"  {path}: {error}")
    if tag:
        rules = [r for r in rules if tag in [t.lower() for t in r.tags]]
    if taxonomy_filter:
        rules = [r for r in rules if (r.taxonomy or "") == taxonomy_filter]
    if not rules:
        click.echo("No rules found.")
        return

    from cauterule.lifecycle.specificity import compute_specificity

    if sort_by == "spec":
        rules = sorted(
            rules,
            key=lambda r: r.specificity if r.specificity is not None else compute_specificity(r)[0],
        )
    for r in rules:
        tags = ",".join(r.tags) if r.tags else "-"
        spec = r.specificity if r.specificity is not None else compute_specificity(r)[0]
        row = (
            f"  {r.id:8s} {r.status:12s} hits={r.hit_count:3d} "
            f"conf={r.confidence:.2f} spec={spec:.2f} tags=[{tags}] "
            f"trigger={r.when.trigger!r}"
        )
        click.echo(row)
