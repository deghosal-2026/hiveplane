from __future__ import annotations

import click

from cauterule.store.manager import StoreManager


@click.command("story")
@click.option("--format", "fmt", default="markdown", help="Output format (markdown/html).")
def story(fmt: str) -> None:  # noqa: ARG001
    """Generate a narrative blog post of the learning journey."""
    store = StoreManager()
    rules = store.list_rules(status="active")

    lines = [
        "# CauterRule Learning Journey",
        "",
        "## Rules Learned",
        "",
    ]
    for r in rules:
        lines.append(f'- **{r.id}**: When "{r.when.trigger}" → {r.do.directive}')
    lines.append("")
    lines.append(f"*Total: {len(rules)} active rules*")

    body = "\n".join(lines)
    click.echo(body)
