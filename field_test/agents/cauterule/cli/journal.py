from __future__ import annotations

from pathlib import Path

import click

from cauterule.store.manager import StoreManager


@click.command("journal")
@click.option("--store-dir", default="rules", help="Rule store directory")
@click.option("--output", "-o", default=None, help="Output file")
def journal(store_dir: str, output: str | None) -> None:
    """Generate learning journal — failure → rule → replay → promotion."""
    from cauterule.observe.journal import generate_journal

    store = StoreManager(base_dir=store_dir)
    md = generate_journal(store)
    if output:
        Path(output).write_text(md, encoding="utf-8")
        click.echo(f"Journal written to {output}")
    else:
        click.echo(md)
