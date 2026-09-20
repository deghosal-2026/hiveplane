from __future__ import annotations

import json

import click

from cauterule.badge import badge_svg
from cauterule.store.manager import StoreManager


@click.command("badge")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
@click.option("--svg", "as_svg", is_flag=True, help="Write cauterule-badge.svg")
@click.option("--url", "as_url", is_flag=True, help="Print shields.io embed URL")
@click.option("--json", "as_json", is_flag=True, help="Emit shields.io endpoint JSON")
@click.option("--output", "-o", default="cauterule-badge.svg", help="SVG output file")
def badge(store_dir: str, as_svg: bool, as_url: bool, as_json: bool, output: str) -> None:
    """Emit the 'N rules learned' badge (SVG, shields URL, or endpoint JSON)."""
    import os

    store = StoreManager(base_dir=os.environ.get("CAUTERULE_STORE", store_dir))
    count = sum(1 for r in store.list_rules() if r.status == "active")
    message = f"{count} rules learned"
    if as_json:
        color = "green" if count > 0 else "blue"
        click.echo(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "label": "Cauterule",
                    "message": message,
                    "color": color,
                }
            )
        )
        return
    if as_url:
        click.echo(
            "https://img.shields.io/endpoint?"
            "url=https://raw.githubusercontent.com/deghosal-2026/Cauterule/main/badge.json"
        )
        return
    svg = badge_svg(count)
    if as_svg:
        from pathlib import Path

        Path(output).write_text(svg, encoding="utf-8")
        click.echo(f"Badge written to {output} ({message})")
    else:
        click.echo(svg)
