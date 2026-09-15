"""HivePlane command-line interface."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

import typer

from hiveplane.core.manifest import ManifestError, load_manifest
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

app = typer.Typer(
    name="hiveplane",
    help="Control plane for production agent fleets.",
    no_args_is_help=True,
)

ManifestArg = Annotated[
    Path,
    typer.Argument(
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to an AgentWorkload manifest YAML file.",
    ),
]


@app.callback()
def _root() -> None:
    """HivePlane control-plane commands."""


@app.command()
def validate(manifest: ManifestArg) -> None:
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


def _post_workload(api_url: str, payload: dict[str, Any]) -> tuple[int, str]:
    """POST a manifest to the control plane; return (status_code, body)."""
    url = f"{api_url.rstrip('/')}/workloads"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


@app.command()
def register(
    manifest: ManifestArg,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report enforcement without persisting."),
    ] = False,
    api_url: Annotated[
        str,
        typer.Option("--api-url", help="Base URL of the control-plane API."),
    ] = "http://localhost:8000",
) -> None:
    """Register an agent workload, or preview enforcement with --dry-run."""
    try:
        workload = load_manifest(manifest)
    except ManifestError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if dry_run:
        summary = RegistryService(InMemoryRegistryStore()).enforcement_summary(workload)
        typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2))
        return

    status_code, body = _post_workload(
        api_url, workload.model_dump(by_alias=True, mode="json")
    )
    if status_code >= 400 or status_code == 0:
        typer.secho(f"registration failed ({status_code}): {body}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(f"OK: registered {workload.name}", fg=typer.colors.GREEN)


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()
