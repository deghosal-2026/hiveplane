from __future__ import annotations

import click

from cauterule.config import config_to_dict, load_config


@click.command("config")
@click.option("--show", is_flag=True, help="Show current configuration.")
@click.option("--set", "set_key", help="Set a config key (key=value).")
def config(show: bool, set_key: str | None) -> None:
    """View or edit CauterRule configuration."""
    if show:
        cfg = load_config()
        click.echo(config_to_dict(cfg))
    elif set_key:
        click.echo(f"Setting config key not yet implemented: {set_key}")
    else:
        cfg = load_config()
        click.echo(config_to_dict(cfg))
