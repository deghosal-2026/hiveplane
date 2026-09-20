from __future__ import annotations

import click


@click.command("share")
@click.argument("rule_id")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
@click.option("--public", is_flag=True, help="Public gist (default: secret)")
@click.option("--description", default="", help="Gist description")
@click.option("--no-provenance", is_flag=True, help="Omit PROVENANCE.json")
@click.option("--open", "open_browser", is_flag=True, help="Open the gist URL in a browser")
@click.option("--filename", default=None, help="Override the rule filename")
@click.option("--yes", is_flag=True, help="Confirm public gist with author email")
def share(
    rule_id: str,
    store_dir: str,
    public: bool,
    description: str,
    no_provenance: bool,
    open_browser: bool,
    filename: str | None,
    yes: bool,
) -> None:
    """Share a single rule as a GitHub gist with full provenance."""
    import os

    from cauterule.packs.share import share_rule as _share

    store = os.environ.get("CAUTERULE_STORE", store_dir)
    try:
        result = _share(
            rule_id,
            store=store,
            public=public,
            description=description,
            include_provenance=not no_provenance,
            open_browser=open_browser,
            filename=filename,
            assume_yes=yes,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    visibility = "public" if public else "secret"
    click.echo(f"Shared {result['rule_id']} as {visibility} gist: {result['url']}")
    if result.get("redacted"):
        click.echo("  note: secrets were redacted before sharing")
    click.echo(f"Teammate can run: cauterule pack install {result['url']}")
