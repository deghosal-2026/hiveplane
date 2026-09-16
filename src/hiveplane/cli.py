"""HivePlane command-line interface."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import typer

from hiveplane.core.manifest import ManifestError, load_manifest
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

app = typer.Typer(
    name="hiveplane",
    help="Control plane for production agent fleets.",
    no_args_is_help=True,
)
certs_app = typer.Typer(help="Inspect and compare certifications.", no_args_is_help=True)
app.add_typer(certs_app, name="certs")

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


def _request(
    method: str, url: str, payload: dict[str, Any] | None = None
) -> tuple[int, str]:
    """Send an HTTP request to the control plane; return (status_code, body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def _post_workload(api_url: str, payload: dict[str, Any]) -> tuple[int, str]:
    """POST a manifest to the control plane; return (status_code, body)."""
    return _request("POST", f"{api_url.rstrip('/')}/workloads", payload)


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


ApiUrl = Annotated[str, typer.Option("--api-url", help="Base URL of the control-plane API.")]


@app.command()
def certify(
    workload: Annotated[str, typer.Argument(help="Workload name to certify.")],
    context: Annotated[
        str, typer.Option("--context", help="Target context: staging or production.")
    ] = "staging",
    corpus: Annotated[
        str | None, typer.Option("--corpus", help="Override the benchmark corpus reference.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8000",
) -> None:
    """Run a workload's benchmark corpus and certify the result."""
    payload: dict[str, Any] = {"workload": workload, "target_context": context}
    if corpus is not None:
        payload["corpus"] = corpus
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/certifications", payload)
    if status_code >= 400 or status_code == 0:
        typer.secho(
            f"certification failed ({status_code}): {body}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1)
    data = json.loads(body)
    typer.secho(
        f"OK: {workload} -> {data['certification']['status']} "
        f"(attestation {data['attestation']['attestation_id']})",
        fg=typer.colors.GREEN,
    )


@certs_app.command("list")
def certs_list(
    workload: Annotated[str | None, typer.Option("--workload")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    api_url: ApiUrl = "http://localhost:8000",
) -> None:
    """List certification records."""
    query = urlencode(
        {key: value for key, value in {"workload": workload, "status": status}.items() if value}
    )
    url = f"{api_url.rstrip('/')}/certifications"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        typer.secho(
            f"failed to list certifications ({status_code}): {body}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    for record in json.loads(body):
        certification = record["certification"]
        typer.echo(
            f"{certification['certification_id']}  {certification['workload_id']}  "
            f"{certification['status']}  {record['attestation']['attestation_id']}"
        )


@certs_app.command("show")
def certs_show(
    certification_id: Annotated[str, typer.Argument(help="Certification id.")],
    api_url: ApiUrl = "http://localhost:8000",
) -> None:
    """Show a certification record, including its attestation."""
    status_code, body = _request(
        "GET", f"{api_url.rstrip('/')}/certifications/{certification_id}"
    )
    if status_code >= 400 or status_code == 0:
        typer.secho(
            f"failed to show certification ({status_code}): {body}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(json.dumps(json.loads(body), indent=2))


@certs_app.command("compare")
def certs_compare(
    before_id: Annotated[str, typer.Argument(help="Earlier certification id.")],
    after_id: Annotated[str, typer.Argument(help="Later certification id.")],
    api_url: ApiUrl = "http://localhost:8000",
) -> None:
    """Compare two certifications and show the per-task regression diff."""
    url = f"{api_url.rstrip('/')}/certifications/compare/{before_id}/{after_id}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        typer.secho(
            f"failed to compare certifications ({status_code}): {body}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    diff = json.loads(body)
    verdict = "BLOCKED" if diff["blocked"] else "ALLOWED"
    typer.echo(f"Verdict: {verdict}")
    typer.echo(f"Passed before: {diff['passed_before']} | Passed now: {diff['passed_after']}")
    for item in diff["regressed"]:
        typer.secho(f"  regressed: {item['task_id']}", fg=typer.colors.RED)
    for item in diff["improved"]:
        typer.secho(f"  improved: {item['task_id']}", fg=typer.colors.GREEN)


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()
