"""HivePlane command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hiveplane.core.manifest import ManifestError, load_manifest

app = typer.Typer(
    name="hiveplane",
    help="Control plane for production agent fleets.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """HivePlane control-plane commands."""


@app.command()
def validate(
    manifest: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to an AgentWorkload manifest YAML file.",
        ),
    ],
) -> None:
    """Validate an agent workload manifest against the strict schema."""
    try:
        workload = load_manifest(manifest)
    except ManifestError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho(
        f"OK: {workload.name} is a valid AgentWorkload "
        f"(certification_status={workload.certification_status.value})",
        fg=typer.colors.GREEN,
    )


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()
