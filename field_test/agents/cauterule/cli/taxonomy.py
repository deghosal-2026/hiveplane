from __future__ import annotations

import click


@click.group("taxonomy")
def taxonomy() -> None:
    """Failure taxonomy classification and backfill."""


@taxonomy.command("backfill")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
def taxonomy_backfill(store_dir: str, dry_run: bool) -> None:
    """Classify store rules missing taxonomy."""
    import os

    from cauterule.taxonomy import backfill

    store = os.environ.get("CAUTERULE_STORE", store_dir)
    report = backfill(store, dry_run=dry_run)
    mode = "would update" if dry_run else "updated"
    if report["updated"]:
        click.echo(f"{mode} ({len(report['updated'])}):")
        for entry in report["updated"]:
            click.echo(f"  {entry}")
    else:
        click.echo("Nothing to update.")
    if report["skipped"]:
        skipped = ", ".join(report["skipped"])
        click.echo(f"Skipped low-confidence ({len(report['skipped'])}): {skipped}")
