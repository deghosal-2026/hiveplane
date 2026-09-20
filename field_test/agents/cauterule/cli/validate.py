from __future__ import annotations

import click

from cauterule.store.validator import validate_store


@click.command("validate")
def validate() -> None:
    """Check rule store integrity."""
    warnings = validate_store()
    if not warnings:
        click.echo("Store integrity check passed.")
    else:
        click.echo("Store warnings:")
        for w in warnings:
            click.echo(f"  - {w}")
