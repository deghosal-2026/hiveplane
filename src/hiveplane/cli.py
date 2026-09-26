"""HivePlane command-line interface."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import typer
import yaml
from pydantic import ValidationError

from hiveplane.core.manifest import ManifestError, load_manifest
from hiveplane.core.triggers import TriggerRule
from hiveplane.registry.models import ToolRegistration
from hiveplane.registry.seeding import derive_tool_registrations
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

app = typer.Typer(
    name="hiveplane",
    help="Control plane for production agent fleets.",
    no_args_is_help=True,
)
certs_app = typer.Typer(help="Inspect and compare certifications.", no_args_is_help=True)
runs_app = typer.Typer(help="Inspect and intervene on runs.", no_args_is_help=True)
approvals_app = typer.Typer(help="Review and resolve approvals.", no_args_is_help=True)
triggers_app = typer.Typer(help="Inspect and add workload triggers.", no_args_is_help=True)
tools_app = typer.Typer(help="Inspect and register MCP tools.", no_args_is_help=True)
reconcile_app = typer.Typer(help="Reconcile desired state (GitOps).", no_args_is_help=True)
app.add_typer(certs_app, name="certs")
app.add_typer(runs_app, name="runs")
app.add_typer(approvals_app, name="approvals")
app.add_typer(triggers_app, name="triggers")
app.add_typer(tools_app, name="tools")
app.add_typer(reconcile_app, name="reconcile")

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


def _load_document(path: Path) -> dict[str, Any]:
    """Load a YAML or JSON document that must contain a mapping."""
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        typer.secho(f"could not read {path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(document, dict):
        typer.secho(f"{path}: expected a mapping document", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return document


def _fail(action: str, status_code: int, body: str) -> None:
    """Report a failed control-plane call and exit non-zero."""
    typer.secho(f"{action} failed ({status_code}): {body}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


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
def submit(
    agent: Annotated[str, typer.Option("--agent", help="Workload name to run.")],
    task: Annotated[
        str, typer.Option("--task", help="JSON object with the task payload.")
    ] = "{}",
    caller: Annotated[str, typer.Option("--caller", help="Who is submitting the run.")] = "cli",
    context: Annotated[
        str,
        typer.Option("--context", help="Target context: sandbox, staging, or production."),
    ] = "sandbox",
    model_identity: Annotated[
        str | None, typer.Option("--model-identity", help="Model identity to pin the run to.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Submit a run for admission and print its id and state."""
    try:
        task_payload = json.loads(task)
    except json.JSONDecodeError as exc:
        typer.secho(f"invalid --task JSON: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(task_payload, dict):
        typer.secho("invalid --task: expected a JSON object", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    payload: dict[str, Any] = {
        "workload": agent,
        "caller": caller,
        "context": context,
        "task": task_payload,
    }
    if model_identity is not None:
        payload["model_identity"] = model_identity
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/runs", payload)
    if status_code >= 400 or status_code == 0:
        _fail("submission", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: submitted {data['id']} ({data['state']})", fg=typer.colors.GREEN)


@app.command()
def certify(
    workload: Annotated[str, typer.Argument(help="Workload name to certify.")],
    context: Annotated[
        str, typer.Option("--context", help="Target context: staging or production.")
    ] = "staging",
    corpus: Annotated[
        str | None, typer.Option("--corpus", help="Override the benchmark corpus reference.")
    ] = None,
    model_identity: Annotated[
        str | None,
        typer.Option("--model-identity", help="Model identity to pin the benchmark to."),
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Run a workload's benchmark corpus and certify the result."""
    payload: dict[str, Any] = {"workload": workload, "target_context": context}
    if corpus is not None:
        payload["corpus"] = corpus
    if model_identity is not None:
        payload["model_identity"] = model_identity
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
    api_url: ApiUrl = "http://localhost:8100",
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
    api_url: ApiUrl = "http://localhost:8100",
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
    api_url: ApiUrl = "http://localhost:8100",
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


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
@runs_app.command("list")
def runs_list(
    workload: Annotated[str | None, typer.Option("--workload", help="Filter by workload.")] = None,
    state: Annotated[str | None, typer.Option("--state", help="Filter by run state.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List runs, optionally filtered by workload and state."""
    query = urlencode(
        {k: v for k, v in {"workload": workload, "state": state}.items() if v}
    )
    url = f"{api_url.rstrip('/')}/runs"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list runs", status_code, body)
    for run in json.loads(body):
        typer.echo(f"{run['id']}  {run['workload_id']}  {run['state']}")


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a run in full, as JSON."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/runs/{run_id}")
    if status_code >= 400 or status_code == 0:
        _fail("show run", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


def _intervene(action: str, run_id: str, api_url: str) -> None:
    """Send an intervention to a run and print the resulting state."""
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/runs/{run_id}/{action}"
    )
    if status_code >= 400 or status_code == 0:
        _fail(f"{action} run", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: {run_id} -> {data['state']}", fg=typer.colors.GREEN)


@runs_app.command("pause")
def runs_pause(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Pause a running run."""
    _intervene("pause", run_id, api_url)


@runs_app.command("resume")
def runs_resume(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Resume a paused run."""
    _intervene("resume", run_id, api_url)


@runs_app.command("stop")
def runs_stop(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Stop a run."""
    _intervene("stop", run_id, api_url)


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
@approvals_app.command("list")
def approvals_list(
    status: Annotated[str | None, typer.Option("--status", help="Filter by status.")] = None,
    workload: Annotated[str | None, typer.Option("--workload", help="Filter by workload.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List approval requests, optionally filtered."""
    query = urlencode(
        {k: v for k, v in {"status": status, "workload": workload}.items() if v}
    )
    url = f"{api_url.rstrip('/')}/approvals"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list approvals", status_code, body)
    for approval in json.loads(body):
        typer.echo(
            f"{approval['approval_id']}  {approval['run_id']}  "
            f"{approval.get('workload') or ''}  {approval['status']}"
        )


def _decide(
    decision: str, approval_id: str, operator: str, reason: str | None, api_url: str
) -> None:
    """Approve or deny an approval and print the resulting status."""
    payload: dict[str, Any] = {"operator": operator}
    if reason is not None:
        payload["reason"] = reason
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/approvals/{approval_id}/{decision}", payload
    )
    if status_code >= 400 or status_code == 0:
        _fail(f"{decision} approval", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: {approval_id} -> {data['status']}", fg=typer.colors.GREEN)


@approvals_app.command("approve")
def approvals_approve(
    approval_id: Annotated[str, typer.Argument(help="Approval id.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator resolving the approval.")],
    reason: Annotated[str | None, typer.Option("--reason", help="Decision rationale.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Approve a request and let the run continue."""
    _decide("approve", approval_id, operator, reason, api_url)


@approvals_app.command("deny")
def approvals_deny(
    approval_id: Annotated[str, typer.Argument(help="Approval id.")],
    operator: Annotated[str, typer.Option("--operator", help="Operator resolving the approval.")],
    reason: Annotated[str | None, typer.Option("--reason", help="Decision rationale.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Deny a request and fail the run."""
    _decide("deny", approval_id, operator, reason, api_url)


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
@triggers_app.command("list")
def triggers_list(
    workload: Annotated[
        str | None, typer.Option("--workload", help="List a workload's legacy trigger rules.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List triggers: the trigger service, or a workload's legacy rules."""
    if workload is not None:
        url = f"{api_url.rstrip('/')}/workloads/{workload}/triggers"
        status_code, body = _request("GET", url)
        if status_code >= 400 or status_code == 0:
            _fail("list triggers", status_code, body)
        for record in json.loads(body):
            typer.echo(f"{record['trigger_id']}  {record['rule']['type']}")
        return
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/triggers")
    if status_code >= 400 or status_code == 0:
        _fail("list triggers", status_code, body)
    for trigger in json.loads(body):
        state = "enabled" if trigger["enabled"] else "disabled"
        typer.echo(f"{trigger['id']}  {trigger['source']}  {trigger['target']['ref']}  {state}")


@triggers_app.command("show")
def triggers_show(
    trigger_id: Annotated[str, typer.Argument(help="Trigger id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a trigger declaration in full, as JSON."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/triggers/{trigger_id}")
    if status_code >= 400 or status_code == 0:
        _fail("show trigger", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@triggers_app.command("create")
def triggers_create(
    file: Annotated[
        Path,
        typer.Option(
            "--file", exists=True, dir_okay=False, readable=True, help="Trigger YAML/JSON."
        ),
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Create (or replace) a trigger-service declaration."""
    payload = _load_document(file)
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/triggers", payload)
    if status_code >= 400 or status_code == 0:
        _fail("create trigger", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: created trigger {data['id']}", fg=typer.colors.GREEN)


@triggers_app.command("test")
def triggers_test(
    trigger_id: Annotated[str, typer.Argument(help="Trigger id.")],
    payload_file: Annotated[
        Path,
        typer.Option(
            "--payload", exists=True, dir_okay=False, readable=True, help="Event payload YAML/JSON."
        ),
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Dry-run a payload: match and render the task, submit no run."""
    payload = _load_document(payload_file)
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/triggers/{trigger_id}/test", {"payload": payload}
    )
    if status_code >= 400 or status_code == 0:
        _fail("test trigger", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@triggers_app.command("replay")
def triggers_replay(
    entry_id: Annotated[str, typer.Argument(help="DLQ entry id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Replay a dead-lettered trigger delivery."""
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/triggers/dlq/{entry_id}/replay"
    )
    if status_code >= 400 or status_code == 0:
        _fail("replay trigger", status_code, body)
    data = json.loads(body)
    typer.secho(
        f"OK: {entry_id} -> {data['outcome']} (run {data.get('run_id')})",
        fg=typer.colors.GREEN,
    )


def _set_trigger_enabled(trigger_id: str, action: str, api_url: str) -> None:
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/triggers/{trigger_id}/{action}"
    )
    if status_code >= 400 or status_code == 0:
        _fail(f"{action} trigger", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: {trigger_id} enabled={data['enabled']}", fg=typer.colors.GREEN)


@triggers_app.command("enable")
def triggers_enable(
    trigger_id: Annotated[str, typer.Argument(help="Trigger id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Enable a trigger."""
    _set_trigger_enabled(trigger_id, "enable", api_url)


@triggers_app.command("disable")
def triggers_disable(
    trigger_id: Annotated[str, typer.Argument(help="Trigger id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Disable a trigger without deleting it."""
    _set_trigger_enabled(trigger_id, "disable", api_url)


@triggers_app.command("add")
def triggers_add(
    workload: Annotated[str, typer.Option("--workload", help="Workload name.")],
    file: Annotated[
        Path,
        typer.Option(
            "--file", exists=True, dir_okay=False, readable=True, help="Trigger YAML/JSON."
        ),
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Add a trigger rule to a workload from a YAML or JSON document."""
    try:
        rule = TriggerRule.model_validate(_load_document(file))
    except ValidationError as exc:
        typer.secho(f"invalid trigger: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    url = f"{api_url.rstrip('/')}/workloads/{workload}/triggers"
    payload = rule.model_dump(mode="json", exclude_none=True)
    status_code, body = _request("POST", url, payload)
    if status_code >= 400 or status_code == 0:
        _fail("add trigger", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: added trigger {data['trigger_id']}", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@tools_app.command("list")
def tools_list(
    trust_level: Annotated[
        str | None, typer.Option("--trust-level", help="Filter by trust level.")
    ] = None,
    mcp_server: Annotated[
        str | None, typer.Option("--mcp-server", help="Filter by MCP server.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List registered MCP tools."""
    query = urlencode(
        {k: v for k, v in {"trust_level": trust_level, "mcp_server": mcp_server}.items() if v}
    )
    url = f"{api_url.rstrip('/')}/tools"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list tools", status_code, body)
    for tool in json.loads(body):
        typer.echo(f"{tool['tool_id']}  {tool['trust_level']}  {tool['name']}")


@tools_app.command("add")
def tools_add(
    file: Annotated[
        Path,
        typer.Option(
            "--file", exists=True, dir_okay=False, readable=True, help="Tool YAML/JSON."
        ),
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Register an MCP tool from a YAML or JSON document."""
    try:
        registration = ToolRegistration.model_validate(_load_document(file))
    except ValidationError as exc:
        typer.secho(f"invalid tool: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/tools", registration.model_dump(mode="json")
    )
    if status_code >= 400 or status_code == 0:
        _fail("add tool", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: registered {data['tool_id']}", fg=typer.colors.GREEN)


@tools_app.command("seed")
def tools_seed(
    workloads_dir: Annotated[
        Path,
        typer.Option(
            "--workloads-dir",
            help="Directory of AgentWorkload manifests whose allowed tools are seeded.",
        ),
    ] = Path("examples/workloads"),
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Register every tool referenced by the workload manifests in a directory (M23, #131).

    Registration is idempotent: a tool already in the registry (HTTP 409) is
    reported as present rather than failing, so the command is safe to re-run.
    """
    if not workloads_dir.is_dir():
        typer.secho(f"{workloads_dir}: not a directory", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    manifests = [
        path
        for path in sorted(workloads_dir.iterdir())
        if path.suffix in (".yaml", ".yml") and path.is_file()
    ]
    registrations: list[ToolRegistration] = []
    for path in manifests:
        try:
            workload = load_manifest(path)
        except ManifestError as exc:
            typer.secho(f"{path}: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        registrations.extend(derive_tool_registrations(workload))

    registered: list[str] = []
    existing: list[str] = []
    for registration in registrations:
        status_code, body = _request(
            "POST",
            f"{api_url.rstrip('/')}/tools",
            registration.model_dump(mode="json"),
        )
        if status_code == HTTPStatus.CREATED:
            registered.append(registration.tool_id)
            typer.secho(f"registered {registration.tool_id}", fg=typer.colors.GREEN)
        elif status_code == HTTPStatus.CONFLICT:
            existing.append(registration.tool_id)
            typer.secho(f"present    {registration.tool_id}", fg=typer.colors.YELLOW)
        else:
            _fail("seed tools", status_code, body)
    typer.secho(
        f"OK: {len(registered)} registered, {len(existing)} already present",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
# Reconcile (GitOps)
# --------------------------------------------------------------------------- #
def _reconcile_payload(
    kind: str,
    path: str | None,
    git_url: str | None,
    git_ref: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Build a reconcile request body for the control-plane API."""
    payload: dict[str, Any] = {"kind": kind, "confirmed": confirmed}
    if kind == "directory":
        if path is None:
            typer.secho("--path is required for a directory source", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        payload["path"] = path
    else:
        if git_url is None:
            typer.secho("--git-url is required for a git source", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        payload["git"] = {"url": git_url, "ref": git_ref}
    return payload


def _print_reconcile_run(data: dict[str, Any]) -> None:
    """Print a reconcile run's outcome and action set."""
    actions = data.get("actions", [])
    typer.echo(f"{data['outcome']}: {len(actions)} action(s)")
    for action in actions:
        typer.echo(
            f"  {action['status']:<8} {action['kind']:<24} {action['object_ref']}"
        )


def _run_reconcile(
    action: str,
    source: str,
    payload: dict[str, Any],
    api_url: str,
) -> None:
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/reconcile/{source}/{action}", payload
    )
    if status_code >= 400 or status_code == 0:
        _fail(f"reconcile {action}", status_code, body)
    _print_reconcile_run(json.loads(body))


@reconcile_app.command("status")
def reconcile_status(
    source: Annotated[str, typer.Option("--source", help="Source id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a source's last revision, last reconcile, and open drift count."""
    status_code, body = _request(
        "GET", f"{api_url.rstrip('/')}/reconcile/{source}"
    )
    if status_code >= 400 or status_code == 0:
        _fail("reconcile status", status_code, body)
    view = json.loads(body)
    typer.echo(
        f"{view['source_id']}  {view['status']}  "
        f"revision={view.get('last_revision') or '-'}  drift={view['open_drift']}"
    )


@reconcile_app.command("plan")
def reconcile_plan(
    source: Annotated[str, typer.Option("--source", help="Source id.")],
    kind: Annotated[
        str, typer.Option("--kind", help="Source kind: directory or git.")
    ] = "directory",
    path: Annotated[str | None, typer.Option("--path", help="Directory source path.")] = None,
    git_url: Annotated[str | None, typer.Option("--git-url", help="Git repository URL.")] = None,
    git_ref: Annotated[str, typer.Option("--git-ref", help="Git ref to pin.")] = "HEAD",
    confirmed: Annotated[
        bool, typer.Option("--confirmed", help="Confirm destructive actions.")
    ] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Dry-run a reconcile: print the plan without mutating state."""
    payload = _reconcile_payload(kind, path, git_url, git_ref, confirmed)
    _run_reconcile("plan", source, payload, api_url)


@reconcile_app.command("apply")
def reconcile_apply(
    source: Annotated[str, typer.Option("--source", help="Source id.")],
    kind: Annotated[
        str, typer.Option("--kind", help="Source kind: directory or git.")
    ] = "directory",
    path: Annotated[str | None, typer.Option("--path", help="Directory source path.")] = None,
    git_url: Annotated[str | None, typer.Option("--git-url", help="Git repository URL.")] = None,
    git_ref: Annotated[str, typer.Option("--git-ref", help="Git ref to pin.")] = "HEAD",
    confirmed: Annotated[
        bool, typer.Option("--confirmed", help="Confirm destructive actions.")
    ] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Apply a reconcile, gated by the configured guardrails."""
    payload = _reconcile_payload(kind, path, git_url, git_ref, confirmed)
    _run_reconcile("apply", source, payload, api_url)


# --------------------------------------------------------------------------- #
# Project scaffolding
# --------------------------------------------------------------------------- #
_INIT_MANIFEST = """\
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: hello-agent
  owner: platform-team
  team: platform
  description: Sample workload created by `hiveplane init`.
spec:
  runtime:
    adapter: raw-worker
    entrypoint: examples.worker:run
  model:
    strategy: tiered
    identity:
      provider: openai
      family: gpt-4o
      version: "2024-08-06"
  certification:
    benchmark_corpus: corpora/hello-agent/v1
    staging_threshold: 0.80
    production_threshold: 0.90
    status: uncertified
  budget:
    per_run_usd: 0.50
    per_day_usd: 5.00
"""

_INIT_CORPUS = """\
id: hello-agent-corpus
version: 1
tasks:
  - id: task-001
    name: greet the world
    input:
      name: world
    expected:
      outcome: "greeting: hello, world"
      required_fields: [greeting]
    check:
      type: exact_match
      field: greeting
      value: "hello, world"
    critical: false
"""

_INIT_README = """\
# HivePlane project

Scaffolded by `hiveplane init`.

- `workloads/hello-agent.yaml` — a sample AgentWorkload manifest.
- `corpora/hello-agent/v1/corpus.yaml` — its sample benchmark corpus.

## Next steps

1. Validate the manifest: `hiveplane validate workloads/hello-agent.yaml`
2. Start the control plane: `docker compose up -d`
3. Register the workload: `hiveplane register workloads/hello-agent.yaml`
4. Certify it: `hiveplane certify hello-agent --context staging`
5. Submit a run: `hiveplane submit --agent hello-agent --task '{{"name": "world"}}'`
"""


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Destination directory.")] = Path(),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing files.")
    ] = False,
) -> None:
    """Scaffold a working project with a sample workload and corpus."""
    files = {
        directory / "workloads" / "hello-agent.yaml": _INIT_MANIFEST,
        directory / "corpora" / "hello-agent" / "v1" / "corpus.yaml": _INIT_CORPUS,
        directory / "README.md": _INIT_README,
    }
    existing = [path for path in files if path.exists()]
    if existing and not force:
        for path in existing:
            typer.secho(f"exists: {path}", fg=typer.colors.RED, err=True)
        typer.secho("refusing to overwrite; re-run with --force", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        typer.secho(f"created {path}", fg=typer.colors.GREEN)
    typer.echo(f"Next: register {directory / 'workloads' / 'hello-agent.yaml'} and certify it.")


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()
