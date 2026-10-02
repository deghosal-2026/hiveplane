"""HivePlane command-line interface."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import typer
import yaml
from pydantic import ValidationError

from hiveplane.certification.errors import CorpusError
from hiveplane.core.manifest import ManifestError, load_manifest
from hiveplane.core.triggers import TriggerRule
from hiveplane.corpus.authoring import (
    add_task,
    corpus_document,
    lint_corpus,
    scaffold_corpus,
    validate_corpus,
)
from hiveplane.corpus.templates import TemplateKind, corpus_template
from hiveplane.policy.models import PolicyPack
from hiveplane.registry.models import ToolRegistration
from hiveplane.registry.seeding import derive_tool_registrations
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.wrap import WrapError, plan_wrap, write_wrap

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
mcp_app = typer.Typer(help="Manage live MCP servers and tools.", no_args_is_help=True)
reconcile_app = typer.Typer(help="Reconcile desired state (GitOps).", no_args_is_help=True)
pipelines_app = typer.Typer(help="Submit and inspect pipeline runs.", no_args_is_help=True)
agents_app = typer.Typer(
    help="Route tasks and call certified agents as tools.", no_args_is_help=True
)
adapters_app = typer.Typer(help="Inspect runtime adapters.", no_args_is_help=True)
drift_app = typer.Typer(
    help="Detect drift, quarantine, and reinstate workloads.", no_args_is_help=True
)
corpus_app = typer.Typer(help="Review production-feedback corpus candidates.", no_args_is_help=True)
eval_app = typer.Typer(
    help="Inspect online-eval samples and production quality.", no_args_is_help=True
)
shadow_app = typer.Typer(help="Inspect shadow-run reports.", no_args_is_help=True)
canary_app = typer.Typer(
    help="Start, inspect, promote, or abort canary rollouts.", no_args_is_help=True
)
experiment_app = typer.Typer(
    help="Start and resolve model experiment campaigns.", no_args_is_help=True
)
policies_app = typer.Typer(help="Lint, publish, and apply team policy packs.", no_args_is_help=True)
health_app = typer.Typer(help="Inspect agent health and SLOs.", no_args_is_help=True)
probes_app = typer.Typer(help="Inspect synthetic probe results.", no_args_is_help=True)
secrets_app = typer.Typer(help="Manage encrypted secrets.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage scoped API keys.", no_args_is_help=True)
auth_app = typer.Typer(help="Operator authentication.", no_args_is_help=True)
workers_app = typer.Typer(help="Inspect and enroll distributed workers.", no_args_is_help=True)
chaos_app = typer.Typer(help="Run chaos/game-day drills.", no_args_is_help=True)
cluster_app = typer.Typer(help="Inspect HA leader state.", no_args_is_help=True)
cost_app = typer.Typer(help="Cost showback and budget views.", no_args_is_help=True)
delivery_app = typer.Typer(help="Fan-out delivery audit.", no_args_is_help=True)
fleet_app = typer.Typer(help="Incident mode: halt and recover the fleet.", no_args_is_help=True)
artifacts_app = typer.Typer(help="Inspect and store run artifacts.", no_args_is_help=True)
app.add_typer(certs_app, name="certs")
app.add_typer(runs_app, name="runs")
app.add_typer(approvals_app, name="approvals")
app.add_typer(triggers_app, name="triggers")
app.add_typer(tools_app, name="tools")
app.add_typer(mcp_app, name="mcp")
app.add_typer(reconcile_app, name="reconcile")
app.add_typer(pipelines_app, name="pipelines")
app.add_typer(agents_app, name="agents")
app.add_typer(adapters_app, name="adapters")
app.add_typer(drift_app, name="drift")
app.add_typer(corpus_app, name="corpus")
app.add_typer(eval_app, name="eval")
app.add_typer(shadow_app, name="shadow")
app.add_typer(canary_app, name="canary")
app.add_typer(experiment_app, name="experiment")
app.add_typer(policies_app, name="policies")
app.add_typer(health_app, name="health")
app.add_typer(probes_app, name="probes")
app.add_typer(secrets_app, name="secrets")
app.add_typer(keys_app, name="keys")
app.add_typer(auth_app, name="auth")
app.add_typer(workers_app, name="workers")
app.add_typer(chaos_app, name="chaos")
app.add_typer(cluster_app, name="cluster")
app.add_typer(cost_app, name="cost")
app.add_typer(delivery_app, name="delivery")
app.add_typer(fleet_app, name="fleet")
app.add_typer(artifacts_app, name="artifacts")

ManifestArg = Annotated[
    Path,
    typer.Argument(
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to an AgentWorkload manifest YAML file.",
    ),
]

TENANT_HEADER = "X-Hiveplane-Tenant"
TEAM_HEADER = "X-Hiveplane-Team"

_scope: dict[str, str | None] = {"tenant": None, "team": None}


@app.callback()
def _root(
    tenant: Annotated[
        str | None,
        typer.Option("--tenant", help="Act as this tenant (X-Hiveplane-Tenant)."),
    ] = None,
    team: Annotated[
        str | None,
        typer.Option("--team", help="Act as this team (X-Hiveplane-Team)."),
    ] = None,
) -> None:
    """HivePlane control-plane commands."""
    _scope["tenant"] = tenant
    _scope["team"] = team


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


def _tenant_headers() -> dict[str, str]:
    """Return the acting tenant/team headers configured for this invocation."""
    headers: dict[str, str] = {}
    tenant = _scope["tenant"] or os.environ.get("HIVEPLANE_TENANT")
    team = _scope["team"]
    if tenant:
        headers[TENANT_HEADER] = tenant
    if team:
        headers[TEAM_HEADER] = team
    return headers


def _request(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
    """Send an HTTP request to the control plane; return (status_code, body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    headers.update(_tenant_headers())
    # Authenticate when the target plane has auth enabled: use an explicit API key or
    # the bootstrap admin key (D-10), so CLI seeding works against an auth-enabled stack.
    key = os.environ.get("HIVEPLANE_API_KEY") or os.environ.get("HIVEPLANE_AUTH__ADMIN_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
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

    status_code, body = _post_workload(api_url, workload.model_dump(by_alias=True, mode="json"))
    if status_code >= 400 or status_code == 0:
        typer.secho(f"registration failed ({status_code}): {body}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(f"OK: registered {workload.name}", fg=typer.colors.GREEN)


ApiUrl = Annotated[str, typer.Option("--api-url", help="Base URL of the control-plane API.")]


@app.command()
def feedback(
    run_id: Annotated[str, typer.Argument(help="Run id to give feedback on.")],
    verdict: Annotated[
        str,
        typer.Option("--verdict", help="good | bad | failed-with-lesson."),
    ],
    notes: Annotated[str, typer.Option("--notes", help="Free-text notes.")] = "",
    operator: Annotated[str, typer.Option("--operator", help="Who is giving feedback.")] = "cli",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Record operator feedback on a terminal run."""
    payload = {"verdict": verdict, "notes": notes, "operator": operator}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/runs/{run_id}/feedback", payload)
    if status_code >= 400 or status_code == 0:
        _fail("record feedback", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@app.command()
def verify(
    attestation_id: Annotated[str, typer.Argument(help="Attestation id to verify.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Verify an attestation publicly (validity + transparency-log inclusion)."""
    url = f"{api_url.rstrip('/')}/attestations/{attestation_id}/verify"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("verify", status_code, body)
    result = json.loads(body)
    typer.echo(json.dumps(result, indent=2))
    if not result.get("valid", False):
        typer.secho(f"attestation {attestation_id!r} is NOT valid", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(f"OK: attestation {attestation_id!r} is valid", fg=typer.colors.GREEN)


@app.command()
def submit(
    agent: Annotated[str, typer.Option("--agent", help="Workload name to run.")],
    task: Annotated[str, typer.Option("--task", help="JSON object with the task payload.")] = "{}",
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
        typer.secho(f"certification failed ({status_code}): {body}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    data = json.loads(body)
    typer.secho(
        f"OK: {workload} -> {data['certification']['status']} "
        f"(attestation {data['attestation']['attestation_id']})",
        fg=typer.colors.GREEN,
    )


@app.command("promote")
def promote(
    workload: Annotated[str, typer.Argument(help="Workload name to promote.")],
    to: Annotated[str, typer.Option("--to", help="Target context to promote into.")] = "production",
    version: Annotated[
        int | None,
        typer.Option("--version", help="Manifest version to promote (default: current)."),
    ] = None,
    recertify: Annotated[
        bool,
        typer.Option(
            "--recertify",
            help="Run the benchmark for the current artifact before promoting.",
        ),
    ] = False,
    operator: Annotated[
        str, typer.Option("--operator", help="Acting operator for the audit trail.")
    ] = "operator",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Promote a workload to production, requiring a certification for its artifact."""
    if recertify:
        payload: dict[str, Any] = {"workload": workload, "operator": operator}
        status_code, body = _request("POST", f"{api_url.rstrip('/')}/promotions/recertify", payload)
        if status_code >= 400 or status_code == 0:
            typer.secho(f"promotion failed ({status_code}): {body}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        data = json.loads(body)
        promotion = data["promotion"]
        if promotion["status"] != "promoted":
            typer.secho(
                f"refused: {promotion['refusal_reason']} "
                f"(changed: {', '.join(promotion['changed_bindings']) or 'none'})",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        typer.secho(
            f"OK: {workload} promoted to {promotion['to_context']} "
            f"(certification {data['certification_id']})",
            fg=typer.colors.GREEN,
        )
        return
    body_payload: dict[str, Any] = {
        "workload": workload,
        "manifest_version": version if version is not None else 1,
        "to_context": to,
        "operator": operator,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/promotions", body_payload)
    if status_code >= 400 or status_code == 0:
        typer.secho(f"promotion refused ({status_code}): {body}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    data = json.loads(body)
    typer.secho(
        f"OK: {workload} v{data['manifest_version']} promoted to {data['to_context']} "
        f"(certification {data['certification_id']})",
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
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/certifications/{certification_id}")
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
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the raw, schema-stable diff JSON.")
    ] = False,
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
    _print_regression_diff(body, as_json=as_json)


@certs_app.command("compare-baseline")
def certs_compare_baseline(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    after_id: Annotated[str, typer.Argument(help="Certification id to compare.")],
    baseline: Annotated[
        str | None,
        typer.Option("--baseline", help="Baseline attestation id (default: last certified)."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the raw, schema-stable diff JSON.")
    ] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Compare a certification to its baseline (last certified unless specified)."""
    query = urlencode({k: v for k, v in {"workload": workload, "baseline": baseline}.items() if v})
    url = f"{api_url.rstrip('/')}/certifications/compare-baseline/{after_id}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        typer.secho(
            f"failed to compare certifications ({status_code}): {body}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    _print_regression_diff(body, as_json=as_json)


def _print_regression_diff(body: str, *, as_json: bool) -> None:
    """Render a regression diff, either raw JSON or a human summary."""
    if as_json:
        typer.echo(json.dumps(json.loads(body), indent=2, sort_keys=True))
        return
    diff = json.loads(body)
    verdict = "BLOCKED" if diff["blocked"] else "ALLOWED"
    typer.echo(f"Verdict: {verdict} ({diff.get('severity', 'none')})")
    typer.echo(f"Passed before: {diff['passed_before']} | Passed now: {diff['passed_after']}")
    for item in diff["regressed"]:
        typer.secho(
            f"  regressed [{item.get('severity', 'none')}]: {item['task_id']}",
            fg=typer.colors.RED,
        )
    for item in diff["improved"]:
        typer.secho(f"  improved: {item['task_id']}", fg=typer.colors.GREEN)
    if diff.get("summary"):
        typer.echo(diff["summary"])


# --------------------------------------------------------------------------- #
# Drift, quarantine, and reinstatement (M34)
# --------------------------------------------------------------------------- #
@drift_app.command("due")
def drift_due(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List workloads whose re-certification window is due or expired."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/drift/due")
    if status_code >= 400 or status_code == 0:
        _fail("list due workloads", status_code, body)
    due = json.loads(body)
    if not due:
        typer.echo("No workloads are due for re-certification.")
        return
    for item in due:
        typer.echo(
            f"{item['workload']}  next={item['next_re_cert_run']}  expiry={item['expiry_state']}"
        )


@drift_app.command("schedules")
def drift_schedules(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List the computed re-certification cadence per workload."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/drift/schedules")
    if status_code >= 400 or status_code == 0:
        _fail("list drift schedules", status_code, body)
    for schedule in json.loads(body):
        typer.echo(
            f"{schedule['workload']}  every {schedule['interval_seconds']}s  "
            f"next={schedule['next_re_cert_run']}"
        )


@drift_app.command("expiries")
def drift_expiries(api_url: ApiUrl = "http://localhost:8100") -> None:
    """Show certification expiry/renewal states."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/drift/expiries")
    if status_code >= 400 or status_code == 0:
        _fail("list expiries", status_code, body)
    for item in json.loads(body):
        typer.echo(f"{item['workload']}  {item['state']}  expires={item['expires_at']}")


def _eval_summary_payload(
    *,
    pass_rate: float,
    tasks_passed: int,
    tasks_failed: int,
    critical_failures: int,
    p95_latency_ms: int,
) -> dict[str, Any]:
    return {
        "pass_rate": pass_rate,
        "tasks_passed": tasks_passed,
        "tasks_failed": tasks_failed,
        "critical_failures": critical_failures,
        "p95_latency_ms": p95_latency_ms,
    }


@drift_app.command("assess")
def drift_assess(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    pass_rate: Annotated[float, typer.Option("--pass-rate")] = 1.0,
    tasks_passed: Annotated[int, typer.Option("--tasks-passed")] = 0,
    tasks_failed: Annotated[int, typer.Option("--tasks-failed")] = 0,
    critical_failures: Annotated[int, typer.Option("--critical-failures")] = 0,
    p95_latency_ms: Annotated[int, typer.Option("--p95-latency-ms")] = 1000,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Assess current performance against the certified baseline."""
    payload = {
        "workload": workload,
        "current": _eval_summary_payload(
            pass_rate=pass_rate,
            tasks_passed=tasks_passed,
            tasks_failed=tasks_failed,
            critical_failures=critical_failures,
            p95_latency_ms=p95_latency_ms,
        ),
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/drift/assess", payload)
    if status_code >= 400 or status_code == 0:
        _fail("assess drift", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@drift_app.command("probe")
def drift_probe(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    target_context: Annotated[str, typer.Option("--context")] = "staging",
    corpus: Annotated[str | None, typer.Option("--corpus")] = None,
    model_identity: Annotated[str | None, typer.Option("--model-identity")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Run a fresh benchmark and assess the workload for drift."""
    payload: dict[str, Any] = {"workload": workload, "target_context": target_context}
    if corpus:
        payload["corpus"] = corpus
    if model_identity:
        payload["model_identity"] = model_identity
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/drift/probe", payload)
    if status_code >= 400 or status_code == 0:
        _fail("probe drift", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@drift_app.command("quarantine")
def drift_quarantine(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    reason: Annotated[str, typer.Option("--reason")] = "operator quarantine",
    operator: Annotated[str, typer.Option("--operator")] = "operator",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Quarantine a workload, revoking production admission."""
    payload = {"workload": workload, "reason": reason, "operator": operator}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/quarantines", payload)
    if status_code >= 400 or status_code == 0:
        _fail("quarantine workload", status_code, body)
    record = json.loads(body)
    typer.secho(
        f"quarantined {record['workload']} ({record['quarantine_id']})",
        fg=typer.colors.GREEN,
    )


@drift_app.command("quarantines")
def drift_quarantines(
    workload: Annotated[str | None, typer.Option("--workload")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List quarantine history and reasons."""
    query = urlencode({k: v for k, v in {"workload": workload}.items() if v})
    url = f"{api_url.rstrip('/')}/quarantines"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list quarantines", status_code, body)
    for record in json.loads(body):
        typer.echo(
            f"{record['quarantine_id']}  {record['workload']}  "
            f"{record['status']}  {record['severity']}  {record['reason']}"
        )


@drift_app.command("reinstate")
def drift_reinstate(
    quarantine_id: Annotated[str, typer.Argument(help="Quarantine id.")],
    operator: Annotated[str, typer.Option("--operator")] = "operator",
    corpus: Annotated[str | None, typer.Option("--corpus")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Re-certify a quarantined workload and resume admission."""
    payload: dict[str, Any] = {"operator": operator}
    if corpus:
        payload["corpus"] = corpus
    url = f"{api_url.rstrip('/')}/quarantines/{quarantine_id}/reinstate"
    status_code, body = _request("POST", url, payload)
    if status_code >= 400 or status_code == 0:
        _fail("reinstate workload", status_code, body)
    record = json.loads(body)
    typer.secho(
        f"reinstated {record['workload']} by {record['reinstated_by']}",
        fg=typer.colors.GREEN,
    )


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
    query = urlencode({k: v for k, v in {"workload": workload, "state": state}.items() if v})
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
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/runs/{run_id}/{action}")
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
    query = urlencode({k: v for k, v in {"status": status, "workload": workload}.items() if v})
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
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/triggers/dlq/{entry_id}/replay")
    if status_code >= 400 or status_code == 0:
        _fail("replay trigger", status_code, body)
    data = json.loads(body)
    typer.secho(
        f"OK: {entry_id} -> {data['outcome']} (run {data.get('run_id')})",
        fg=typer.colors.GREEN,
    )


def _set_trigger_enabled(trigger_id: str, action: str, api_url: str) -> None:
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/triggers/{trigger_id}/{action}")
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
        Path | None,
        typer.Option("--file", exists=True, dir_okay=False, readable=True, help="Tool YAML/JSON."),
    ] = None,
    server: Annotated[
        str | None,
        typer.Option("--server", help="Connect an MCP server: stdio://cmd, http(s)://url."),
    ] = None,
    trust: Annotated[
        str, typer.Option("--trust", help="Trust level for discovered tools.")
    ] = "read_only",
    actor: Annotated[str, typer.Option("--actor", help="Onboarding operator.")] = "cli",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Onboard tools from a live MCP server, or register one from a document."""
    if server is not None:
        _add_from_server(server, trust, actor, api_url)
        return
    if file is None:
        typer.secho("provide --server or --file", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
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
        typer.echo(f"  {action['status']:<8} {action['kind']:<24} {action['object_ref']}")


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
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/reconcile/{source}")
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
# Pipelines (multi-agent orchestration)
# --------------------------------------------------------------------------- #
def _print_timeline(data: dict[str, Any]) -> None:
    """Print a pipeline run's state and per-node timeline."""
    typer.echo(
        f"{data['pipeline_id']}  {data['state']}  "
        f"${data['spent_usd']:.4f}/${data['budget_usd']:.2f}"
    )
    for node in data["nodes"]:
        typer.echo(f"  {node['status']:<18} {node['node_id']:<20} ${node['cost_usd']:.4f}")


@pipelines_app.command("list")
def pipelines_list(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List registered pipeline specs."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/pipelines")
    if status_code >= 400 or status_code == 0:
        _fail("list pipelines", status_code, body)
    for spec in json.loads(body):
        typer.echo(f"{spec['id']}  v{spec['version']}  {spec['name']}")


@pipelines_app.command("show")
def pipelines_show(
    pipeline_id: Annotated[str, typer.Argument(help="Pipeline id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a pipeline spec as JSON."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/pipelines/{pipeline_id}")
    if status_code >= 400 or status_code == 0:
        _fail("show pipeline", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@pipelines_app.command("submit")
def pipelines_submit(
    pipeline_id: Annotated[str, typer.Option("--pipeline", help="Pipeline id.")],
    inputs: Annotated[
        Path | None,
        typer.Option(
            "--inputs",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Inputs YAML/JSON.",
        ),
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Submit a pipeline run and print its timeline."""
    payload = {"inputs": _load_document(inputs) if inputs is not None else {}}
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/pipelines/{pipeline_id}/runs", payload
    )
    if status_code >= 400 or status_code == 0:
        _fail("submit pipeline", status_code, body)
    _print_timeline(json.loads(body))


@pipelines_app.command("status")
def pipelines_status(
    run_id: Annotated[str, typer.Argument(help="Pipeline run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a pipeline run's parent state and node timeline."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/pipeline-runs/{run_id}")
    if status_code >= 400 or status_code == 0:
        _fail("pipeline status", status_code, body)
    _print_timeline(json.loads(body))


@pipelines_app.command("retry")
def pipelines_retry(
    run_id: Annotated[str, typer.Option("--run", help="Pipeline run id.")],
    node_id: Annotated[str, typer.Option("--node", help="Node id to retry.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Retry a failed pipeline node."""
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/pipeline-runs/{run_id}/nodes/{node_id}/retry"
    )
    if status_code >= 400 or status_code == 0:
        _fail("retry pipeline node", status_code, body)
    _print_timeline(json.loads(body))


@app.command("route")
def route_task(
    task: Annotated[str, typer.Argument(help="Plain-language task to route.")],
    context: Annotated[
        str, typer.Option("--context", help="Target context: sandbox|staging|production.")
    ] = "staging",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Route a task to the best certified workload, or show the refusal."""
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/route", {"task": task, "context": context}
    )
    if status_code >= 400 or status_code == 0:
        _fail("route task", status_code, body)
    data = json.loads(body)
    if data["outcome"] == "routed":
        top = data["candidates"][0]
        typer.echo(f"routed to {data['chosen']}  (score {top['score']:.2f})")
        typer.echo(f"classifier: {data['classifier_model']}")
        return
    typer.echo(f"refused: {data['reason']}")
    for candidate in data["candidates"]:
        typer.echo(f"  {candidate['score']:.2f}  {candidate['workload']}")


@agents_app.command("list")
def agents_list(
    context: Annotated[
        str, typer.Option("--context", help="Target context: sandbox|staging|production.")
    ] = "staging",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List certified workloads exposed as callable tools."""
    status_code, body = _request(
        "GET", f"{api_url.rstrip('/')}/agent-tools?{urlencode({'context': context})}"
    )
    if status_code >= 400 or status_code == 0:
        _fail("list agent tools", status_code, body)
    for tool in json.loads(body):
        typer.echo(f"{tool['tool_id']}  {tool['description']}")


@agents_app.command("invoke")
def agents_invoke(
    tool_id: Annotated[str, typer.Argument(help="Tool id, e.g. agent.triage.")],
    caller_run_id: Annotated[str, typer.Option("--caller-run", help="Calling run id.")],
    context: Annotated[
        str, typer.Option("--context", help="Target context: sandbox|staging|production.")
    ] = "staging",
    task: Annotated[
        Path | None,
        typer.Option("--task", exists=True, dir_okay=False, readable=True, help="Task JSON."),
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Invoke a certified workload as a nested agent tool."""
    payload = {
        "caller_run_id": caller_run_id,
        "context": context,
        "task": _load_document(task) if task is not None else {},
    }
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/agent-tools/{tool_id}/invoke", payload
    )
    if status_code >= 400 or status_code == 0:
        _fail("invoke agent tool", status_code, body)
    data = json.loads(body)
    typer.echo(f"{data['tool_id']} -> {data['nested_run_id']}  (depth {data['depth']})")


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
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
    template: Annotated[
        str | None,
        typer.Option(
            "--template",
            help="Seed the corpus from a template (repo-agent|triage|generation|classification).",
        ),
    ] = None,
) -> None:
    """Scaffold a working project with a sample workload and corpus."""
    corpus_content = _INIT_CORPUS
    if template is not None:
        try:
            kind = TemplateKind(template)
        except ValueError as exc:
            typer.secho(f"unknown template: {template}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        corpus_content = corpus_document(corpus_template(kind, corpus_id="hello-agent-corpus"))
    files = {
        directory / "workloads" / "hello-agent.yaml": _INIT_MANIFEST,
        directory / "corpora" / "hello-agent" / "v1" / "corpus.yaml": corpus_content,
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


@app.command("wrap")
def wrap(
    path: Annotated[Path, typer.Argument(help="Path to the app to inspect.")],
    framework: Annotated[
        str,
        typer.Option(
            "--framework",
            help="auto|langgraph|pydanticai|openai-agents|crewai (default auto).",
        ),
    ] = "auto",
    out: Annotated[
        Path | None, typer.Option("--out", help="Output directory for generated files.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan without writing.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite a non-empty output directory.")
    ] = False,
) -> None:
    """Inspect an existing app and generate a workload manifest + adapter scaffold."""
    requested = None if framework == "auto" else framework
    try:
        plan = plan_wrap(path, framework=requested)
    except WrapError as exc:
        typer.secho(f"wrap: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if dry_run:
        typer.echo(f"framework: {plan.framework}")
        typer.echo(f"entrypoint: {plan.entrypoint}")
        for name in plan.files:
            typer.echo(f"  would write {name}")
        return
    out_dir = out or (Path.cwd() / f"{path.name}-hiveplane")
    try:
        written = write_wrap(plan, out_dir, force=force)
    except WrapError as exc:
        typer.secho(f"wrap: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    for target in written:
        typer.secho(f"created {target}", fg=typer.colors.GREEN)
    typer.echo("Generated drafts are inert; register and certify them to go live.")


@adapters_app.command("list")
def adapters_list(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List runtime adapters and their contract/capabilities."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/adapters")
    if status_code >= 400 or status_code == 0:
        _fail("list adapters", status_code, body)
    for adapter in json.loads(body):
        caps = adapter["capabilities"]
        typer.echo(
            f"{adapter['name']:<14} contract v{adapter['contract_version']} "
            f"conformance v{adapter['conformance_version']} "
            f"streaming={caps['streaming']} pause_resume={caps['pause_resume']}"
        )


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()


@corpus_app.command("candidates")
def corpus_candidates(
    workload: Annotated[str | None, typer.Option("--workload")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List production-feedback corpus candidates."""
    query = urlencode(
        {key: value for key, value in {"workload": workload, "status": status}.items() if value}
    )
    url = f"{api_url.rstrip('/')}/corpus/candidates"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list corpus candidates", status_code, body)
    for candidate in json.loads(body):
        typer.echo(
            f"{candidate['candidate_id']}  {candidate['workload_id']}  "
            f"{candidate['status']}  run={candidate['source_run_id']}"
        )


@corpus_app.command("approve")
def corpus_approve(
    candidate_id: Annotated[str, typer.Argument(help="Candidate id.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Reviewer id.")] = "cli",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Approve a candidate, staging it for the next corpus version."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/corpus/candidates/{candidate_id}/approve",
        {"reviewer": reviewer},
    )
    if status_code >= 400 or status_code == 0:
        _fail("approve candidate", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@corpus_app.command("reject")
def corpus_reject(
    candidate_id: Annotated[str, typer.Argument(help="Candidate id.")],
    reason: Annotated[str, typer.Option("--reason", help="Why it is rejected.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Reviewer id.")] = "cli",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Reject a candidate, archiving it with a reason."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/corpus/candidates/{candidate_id}/reject",
        {"reviewer": reviewer, "reason": reason},
    )
    if status_code >= 400 or status_code == 0:
        _fail("reject candidate", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@eval_app.command("samples")
def eval_samples(
    workload: Annotated[str | None, typer.Option("--workload")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List production runs selected for online evaluation."""
    query = urlencode({"workload": workload}) if workload else ""
    url = f"{api_url.rstrip('/')}/eval/samples"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list eval samples", status_code, body)
    for sample in json.loads(body):
        typer.echo(
            f"{sample['sample_id']}  run={sample['run_id']}  rubric v{sample['rubric_version']}"
        )


@eval_app.command("quality")
def eval_quality(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a workload's rolling-window production quality score."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/workloads/{workload}/quality")
    if status_code >= 400 or status_code == 0:
        _fail("show quality", status_code, body)
    quality = json.loads(body)
    typer.echo(
        f"{quality['workload_id']}  mean={quality['mean_score']:.2f}  "
        f"samples={quality['sample_count']}  dip={quality['dip']}"
    )


@shadow_app.command("report")
def shadow_report(
    shadow_run_id: Annotated[str, typer.Argument(help="Shadow run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show the production-vs-candidate outcome diff for a shadow run."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/shadow/{shadow_run_id}/report")
    if status_code >= 400 or status_code == 0:
        _fail("shadow report", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@canary_app.command("start")
def canary_start(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    candidate: Annotated[int, typer.Option("--candidate", help="Candidate version.")],
    pct: Annotated[int, typer.Option("--pct", help="Traffic percentage (0-100).")] = 10,
    window: Annotated[int, typer.Option("--window", help="Evaluation window (s).")] = 3600,
    min_sample: Annotated[int, typer.Option("--min-sample")] = 30,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Start a canary rollout routing a percentage of triggers to a candidate."""
    payload = {
        "workload_id": workload,
        "candidate_version": candidate,
        "traffic_pct": pct,
        "window_seconds": window,
        "min_sample": min_sample,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/canary", payload)
    if status_code >= 400 or status_code == 0:
        _fail("start canary", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@canary_app.command("status")
def canary_status(
    rollout_id: Annotated[str, typer.Argument(help="Canary rollout id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a canary's state with window metrics and sample counts."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/canary/{rollout_id}")
    if status_code >= 400 or status_code == 0:
        _fail("canary status", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


def _canary_decision(
    rollout_id: str, action: str, operator: str, reason: str | None, api_url: str
) -> None:
    payload: dict[str, Any] = {"operator": operator}
    if reason is not None:
        payload["reason"] = reason
    status_code, body = _request(
        "POST", f"{api_url.rstrip('/')}/canary/{rollout_id}/{action}", payload
    )
    if status_code >= 400 or status_code == 0:
        _fail(f"canary {action}", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@canary_app.command("promote")
def canary_promote(
    rollout_id: Annotated[str, typer.Argument(help="Canary rollout id.")],
    operator: Annotated[str, typer.Option("--operator")] = "cli",
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Manually promote a canary candidate."""
    _canary_decision(rollout_id, "promote", operator, reason, api_url)


@canary_app.command("abort")
def canary_abort(
    rollout_id: Annotated[str, typer.Argument(help="Canary rollout id.")],
    operator: Annotated[str, typer.Option("--operator")] = "cli",
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Manually abort a canary and roll traffic back."""
    _canary_decision(rollout_id, "abort", operator, reason, api_url)


@experiment_app.command("start")
def experiment_start(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    arms: Annotated[str, typer.Option("--arms", help="Comma-separated model identities.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Start a model experiment campaign across >= 2 model configurations."""
    payload = {"workload_id": workload, "arms": [arm for arm in arms.split(",") if arm]}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/experiments", payload)
    if status_code >= 400 or status_code == 0:
        _fail("start experiment", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@tools_app.command("disable")
def tools_disable(
    tool_id: Annotated[str, typer.Argument(help="Tool id to disable.")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Disable a tool fleet-wide (kill switch)."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/tools/{tool_id}/disable",
        {"reason": reason},
    )
    if status_code >= 400 or status_code == 0:
        _fail("disable tool", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@tools_app.command("enable")
def tools_enable(
    tool_id: Annotated[str, typer.Argument(help="Tool id to re-enable.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Re-enable a disabled tool."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/tools/{tool_id}/enable",
        {},
    )
    if status_code >= 400 or status_code == 0:
        _fail("enable tool", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


def _load_policy_pack(path: Path) -> PolicyPack:
    document = _load_document(path)
    try:
        return PolicyPack.model_validate(document)
    except ValidationError as exc:
        typer.secho(f"{path}: invalid policy pack: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@policies_app.command("lint")
def policies_lint(
    pack_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Policy pack YAML."),
    ],
) -> None:
    """Lint a policy pack (schema, inheritance references, cycles)."""
    from hiveplane.policy.pack_registry import PolicyPackRegistry
    from hiveplane.policy.packs import InMemoryPolicyPackStore

    pack = _load_policy_pack(pack_path)
    registry = PolicyPackRegistry(InMemoryPolicyPackStore())
    result = registry.lint(pack, available={pack.metadata.name: pack})
    if not result.valid:
        for issue in result.issues:
            typer.secho(f"- {issue}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(
        f"OK: {pack.metadata.name} ({pack.metadata.version}) is valid",
        fg=typer.colors.GREEN,
    )


@policies_app.command("publish")
def policies_publish(
    pack_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Policy pack YAML."),
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Publish an immutable policy pack version."""
    pack = _load_policy_pack(pack_path)
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/policy-packs",
        pack.model_dump(mode="json", by_alias=True),
    )
    if status_code >= 400 or status_code == 0:
        _fail("publish policy pack", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@policies_app.command("apply")
def policies_apply(
    name: Annotated[str, typer.Argument(help="Published pack name.")],
    team: Annotated[str, typer.Option("--team", help="Team to pin the pack to.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Apply (pin) a published pack and its inherited chain to a team."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/policy-packs/{name}/apply",
        {"team": team},
    )
    if status_code >= 400 or status_code == 0:
        _fail("apply policy pack", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@health_app.command("list")
def health_list(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List fleet health across workloads."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/health")
    if status_code >= 400 or status_code == 0:
        _fail("list health", status_code, body)
    for health in json.loads(body):
        typer.echo(
            f"{health['workload']}  {health['status']}  "
            f"fail={health['failure_rate']:.2f}  ready={health['readiness']}"
        )


@health_app.command("show")
def health_show(
    workload: Annotated[str, typer.Argument(help="Workload name.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a workload's health model, SLOs, and burn."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/health/workloads/{workload}")
    if status_code >= 400 or status_code == 0:
        _fail("show health", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@probes_app.command("list")
def probes_list(
    workload: Annotated[str | None, typer.Option("--workload")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List synthetic probe results."""
    query = urlencode({"workload_id": workload}) if workload else ""
    url = f"{api_url.rstrip('/')}/health/probes"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list probes", status_code, body)
    for probe in json.loads(body):
        typer.echo(
            f"{probe['probe_id']}  {probe['workload_id']}  "
            f"{probe['status']}  {probe['latency_ms']}ms"
        )


def _parse_server_uri(value: str) -> dict[str, object]:
    """Parse an MCP server URI into a connect request payload."""
    import shlex

    if value.startswith("stdio://"):
        parts = shlex.split(value[len("stdio://") :])
        if not parts:
            typer.secho("stdio server needs a command", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        return {"kind": "stdio", "target": parts[0], "args": parts[1:]}
    if value.startswith(("http://", "https://")):
        return {"kind": "http", "target": value, "args": []}
    if value.startswith("sse://"):
        return {"kind": "sse", "target": value[len("sse://") :], "args": []}
    typer.secho(f"unsupported server URI: {value!r}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def _add_from_server(server: str, trust: str, actor: str, api_url: str) -> None:
    endpoint = _parse_server_uri(server)
    base = api_url.rstrip("/")
    status_code, body = _request("POST", f"{base}/mcp/servers", {"endpoint": endpoint})
    if status_code >= 400 or status_code == 0:
        _fail("connect server", status_code, body)
    connected = json.loads(body)
    status_code, body = _request("GET", f"{base}/mcp/tools")
    if status_code >= 400 or status_code == 0:
        _fail("list tools", status_code, body)
    tools = [tool for tool in json.loads(body) if tool["server_id"] == connected["server_id"]]
    for tool in tools:
        status_code, body = _request(
            "POST",
            f"{base}/mcp/tools/{tool['tool_id']}/onboard",
            {"trust_level": trust, "actor": actor},
        )
        if status_code >= 400 or status_code == 0:
            _fail("onboard tool", status_code, body)
        typer.secho(f"onboarded {tool['tool_id']} ({tool['tool_name']})", fg=typer.colors.GREEN)


@tools_app.command("show")
def tools_show(
    tool_id: Annotated[str, typer.Argument(help="Stable tool id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show one live MCP tool by its stable id."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/mcp/tools/{tool_id}")
    if status_code >= 400 or status_code == 0:
        _fail("show tool", status_code, body)
    tool = json.loads(body)
    typer.echo(f"{tool['tool_id']}  {tool['tool_name']}  {tool['status']}  {tool['trust_level']}")


@tools_app.command("remove")
def tools_remove(
    tool_id: Annotated[str, typer.Argument(help="Stable tool id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Retire a live MCP tool (its id is never reused)."""
    status_code, body = _request("DELETE", f"{api_url.rstrip('/')}/mcp/tools/{tool_id}")
    if status_code >= 400 or status_code == 0:
        _fail("remove tool", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: retired {data['tool_id']}", fg=typer.colors.GREEN)


@mcp_app.command("servers")
def mcp_servers(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List registered live MCP servers."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/mcp/servers")
    if status_code >= 400 or status_code == 0:
        _fail("list servers", status_code, body)
    for server in json.loads(body):
        typer.echo(f"{server['server_id']}  {server['status']}  {server['fingerprint']}")


@mcp_app.command("tools")
def mcp_tools(
    status_filter: Annotated[
        str | None, typer.Option("--status", help="Filter by tool status.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List live MCP tools (discovered/active/absent/retired)."""
    query = urlencode({"status": status_filter}) if status_filter else ""
    url = f"{api_url.rstrip('/')}/mcp/tools"
    if query:
        url = f"{url}?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list MCP tools", status_code, body)
    for tool in json.loads(body):
        typer.echo(f"{tool['tool_id']}  {tool['tool_name']}  {tool['status']}")


# --------------------------------------------------------------------------- #
# Secrets, keys, and auth (M45)
# --------------------------------------------------------------------------- #
def _secret_value(value: str | None, from_env: str | None) -> str:
    if value is not None:
        return value
    if from_env is not None:
        resolved = os.environ.get(from_env)
        if resolved is None:
            typer.secho(
                f"environment variable {from_env!r} is not set",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        return resolved
    typer.secho("provide --value or --from-env", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


@secrets_app.command("put")
def secrets_put(
    name: Annotated[str, typer.Argument(help="Secret name.")],
    value: Annotated[str | None, typer.Option("--value", help="Secret value (write-only).")] = None,
    from_env: Annotated[
        str | None, typer.Option("--from-env", help="Read the value from an env var.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Create a secret; the value is never echoed or returned."""
    payload = {"name": name, "value": _secret_value(value, from_env)}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/secrets", payload)
    if status_code >= 400 or status_code == 0:
        _fail("put secret", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: {data['name']}@v{data['current_version']}", fg=typer.colors.GREEN)


@secrets_app.command("rotate")
def secrets_rotate(
    name: Annotated[str, typer.Argument(help="Secret name.")],
    value: Annotated[str | None, typer.Option("--value", help="New secret value.")] = None,
    from_env: Annotated[
        str | None, typer.Option("--from-env", help="Read the value from an env var.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Rotate a secret to a new version (takes effect on the next run)."""
    payload = {"name": name, "value": _secret_value(value, from_env)}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/secrets/{name}/rotate", payload)
    if status_code >= 400 or status_code == 0:
        _fail("rotate secret", status_code, body)
    data = json.loads(body)
    typer.secho(f"OK: {data['name']}@v{data['current_version']}", fg=typer.colors.GREEN)


@secrets_app.command("list")
def secrets_list(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List secret metadata (never plaintext)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/secrets")
    if status_code >= 400 or status_code == 0:
        _fail("list secrets", status_code, body)
    for secret in json.loads(body):
        typer.echo(f"{secret['name']}  v{secret['current_version']}  {secret['versions']}")


@secrets_app.command("show")
def secrets_show(
    name: Annotated[str, typer.Argument(help="Secret name.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show one secret's metadata."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/secrets/{name}")
    if status_code >= 400 or status_code == 0:
        _fail("show secret", status_code, body)
    secret = json.loads(body)
    typer.echo(
        f"{secret['name']}  current=v{secret['current_version']}  versions={secret['versions']}"
    )


@keys_app.command("create")
def keys_create(
    role: Annotated[str, typer.Option("--role", help="admin|approver|viewer.")] = "viewer",
    scope: Annotated[
        list[str] | None, typer.Option("--scope", help="Narrowing scope (repeatable).")
    ] = None,
    label: Annotated[str | None, typer.Option("--label")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Issue a scoped API key; the token is shown exactly once."""
    payload: dict[str, object] = {"role": role, "label": label}
    if scope:
        payload["scopes"] = scope
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/keys", payload)
    if status_code >= 400 or status_code == 0:
        _fail("create key", status_code, body)
    data = json.loads(body)
    typer.secho(f"key id: {data['key_id']}", fg=typer.colors.GREEN)
    typer.echo(f"token (store it now): {data['token']}")


@keys_app.command("list")
def keys_list(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List API keys (hashed only)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/keys")
    if status_code >= 400 or status_code == 0:
        _fail("list keys", status_code, body)
    for key in json.loads(body):
        typer.echo(f"{key['key_id']}  {key['role']}  revoked={bool(key['revoked_at'])}")


@keys_app.command("revoke")
def keys_revoke(
    key_id: Annotated[str, typer.Argument(help="API key id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Revoke an API key."""
    status_code, body = _request("DELETE", f"{api_url.rstrip('/')}/keys/{key_id}")
    if status_code >= 400 or status_code == 0:
        _fail("revoke key", status_code, body)
    typer.secho(f"OK: revoked {key_id}", fg=typer.colors.GREEN)


@auth_app.command("whoami")
def auth_whoami(api_url: ApiUrl = "http://localhost:8100") -> None:
    """Show the verified caller identity."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/auth/whoami")
    if status_code >= 400 or status_code == 0:
        _fail("whoami", status_code, body)
    identity = json.loads(body)
    typer.echo(f"{identity['operator_id']}  {identity['role']}  tenant={identity['tenant_id']}")


# --------------------------------------------------------------------------- #
# Distributed workers (M46)
# --------------------------------------------------------------------------- #
@workers_app.command("list")
def workers_list(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show the worker fleet: state, load, and active leases."""
    query = urlencode({"tenant_id": tenant_id})
    url = f"{api_url.rstrip('/')}/workers?{query}"
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("list workers", status_code, body)
    for entry in json.loads(body):
        worker = entry["worker"]
        typer.echo(
            f"{worker['worker_id']}  {worker['state']}  load={entry['load']}  "
            f"leases={len(entry['active_leases'])}"
        )


@workers_app.command("enroll")
def workers_enroll(
    worker_id: Annotated[str, typer.Option("--worker-id")],
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Enroll a worker: issue its signed identity token (shown once)."""
    status_code, body = _request(
        "POST",
        f"{api_url.rstrip('/')}/workers/enroll",
        {"worker_id": worker_id, "tenant_id": tenant_id},
    )
    if status_code >= 400 or status_code == 0:
        _fail("enroll worker", status_code, body)
    data = json.loads(body)
    typer.secho(f"token id: {data['record']['token_id']}", fg=typer.colors.GREEN)
    typer.echo(f"token (store it now): {data['token']}")


@app.command("worker")
def worker_run(
    worker_id: Annotated[str, typer.Option("--worker-id")],
    token: Annotated[str, typer.Option("--token", help="Worker identity token.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Run a worker daemon process (registers and reports liveness)."""
    from hiveplane.worker.daemon import WorkerDaemon
    from hiveplane.worker.factory import build_worker_registry

    class _NoopRunner:
        def run(self, assignment: object) -> bool:
            return True

    registry = build_worker_registry()
    daemon = WorkerDaemon(registry, worker_id=worker_id, token=token, runner=_NoopRunner())
    daemon.enroll_and_register()
    daemon.heartbeat(running=0, max_concurrency=1)
    typer.secho(f"worker {worker_id} registered and ready", fg=typer.colors.GREEN)


@app.command("queue")
def queue_show(
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show queue depth, QoS/priority breakdown, and running load."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/queue")
    if status_code >= 400 or status_code == 0:
        _fail("queue", status_code, body)
    snapshot = json.loads(body)
    typer.echo(f"depth={snapshot['depth']}  by_qos={snapshot['by_qos']}")
    for waiting in snapshot["waiting"]:
        typer.echo(
            f"  {waiting['task_id']}  {waiting['workload']}  {waiting['qos']}  {waiting['reason']}"
        )


# --------------------------------------------------------------------------- #
# HA and chaos (M48)
# --------------------------------------------------------------------------- #
@cluster_app.command("leader")
def cluster_leader(api_url: ApiUrl = "http://localhost:8100") -> None:
    """Show the current controller leader and fencing epoch."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/cluster/leader")
    if status_code >= 400 or status_code == 0:
        _fail("cluster leader", status_code, body)
    data = json.loads(body)
    typer.echo(f"leader={data['leader_id']}  epoch={data['epoch']}")


@chaos_app.command("run")
def chaos_run(
    kind: Annotated[
        str,
        typer.Argument(help="kill-worker|revoke-cert|exhaust-budget|inject-tool-failure"),
    ],
    scope_ref: Annotated[str, typer.Option("--scope-ref")] = "sandbox",
    production: Annotated[bool, typer.Option("--production")] = False,
    allow_production: Annotated[bool, typer.Option("--allow-production")] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Run a seeded chaos drill and print its pass/fail report."""
    payload = {
        "kind": kind,
        "scope": "tenant" if production else "sandbox",
        "scope_ref": scope_ref,
        "production": production,
        "allow_production": allow_production,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/chaos/drills", payload)
    if status_code >= 400 or status_code == 0:
        _fail("run drill", status_code, body)
    report = json.loads(body)
    colour = typer.colors.GREEN if report["verdict"] == "pass" else typer.colors.RED
    typer.secho(f"{report['verdict']}: {report['injected']} -> {report['observed']}", fg=colour)


@chaos_app.command("drills")
def chaos_drills(api_url: ApiUrl = "http://localhost:8100") -> None:
    """List chaos drill reports (newest first)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/chaos/drills")
    if status_code >= 400 or status_code == 0:
        _fail("list drills", status_code, body)
    for report in json.loads(body):
        typer.echo(f"{report['kind']}  {report['verdict']}  {report['observed']}")


# --------------------------------------------------------------------------- #
# Cost showback (M49)
# --------------------------------------------------------------------------- #
@cost_app.command("showback")
def cost_showback(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    period: Annotated[str, typer.Option("--period", help="day|week|month")] = "month",
    group_by: Annotated[str, typer.Option("--group-by", help="team|workload")] = "team",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show cost-per-completed-task by team or workload."""
    query = urlencode({"tenant_id": tenant_id, "period": period, "group_by": group_by})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/cost/showback?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("cost showback", status_code, body)
    report = json.loads(body)
    typer.echo(
        f"period={report['period_key']}  total=${report['total_cost_usd']:.2f}  "
        f"cpct=${report['fleet_cpct']:.2f}  unattributed={report['unattributed']}"
    )
    for row in report["rows"]:
        label = row.get("team_id") or row.get("workload_id")
        typer.echo(
            f"  {label}  ${row['total_cost_usd']:.2f}  "
            f"completed={row['completed_tasks']}  cpct=${row['cost_per_completed_task']:.2f}"
        )


@delivery_app.command("audit")
def delivery_audit(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show the fan-out delivery audit (destination, attempts, status)."""
    query = urlencode({"tenant_id": tenant_id})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/delivery/audit?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("delivery audit", status_code, body)
    for attempt in json.loads(body):
        typer.echo(
            f"{attempt['channel']}  {attempt['target']}  {attempt['status']}  "
            f"attempts={attempt['attempts']}"
        )


@cost_app.command("forecast")
def cost_forecast(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    period: Annotated[str, typer.Option("--period", help="day|week|month")] = "month",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show the burn forecast and overrun prediction."""
    query = urlencode({"tenant_id": tenant_id, "period": period})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/cost/forecast?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("cost forecast", status_code, body)
    data = json.loads(body)
    typer.echo(
        f"spent=${data['spent_usd']:.2f}  projected=${data['projected_usd']:.2f}  "
        f"overrun=${data['projected_overrun_usd']:.2f}  p={data['overrun_probability']:.2f}"
    )


@cost_app.command("roi")
def cost_roi(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    period: Annotated[str, typer.Option("--period", help="day|week|month")] = "month",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show fleet ROI and expensive-but-low-value flags."""
    query = urlencode({"tenant_id": tenant_id, "period": period})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/cost/roi/fleet?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("cost roi", status_code, body)
    report = json.loads(body)
    typer.echo(f"fleet_roi={report['fleet_roi']:.2f}")
    for row in report["rows"]:
        flag = "FLAG" if row["expensive_low_value"] else "ok"
        typer.echo(f"  {row['workload_id']}  roi={row['roi']:.2f}  {flag}")


@fleet_app.command("pause")
def fleet_pause(
    reason: Annotated[str | None, typer.Option("--reason", help="Why the fleet is halted.")] = None,
    actor: Annotated[str, typer.Option("--actor", help="Attributed operator.")] = "cli",
    scope: Annotated[
        str, typer.Option("--scope", help="Halt scope: fleet|tenant|workload.")
    ] = "fleet",
    scope_ref: Annotated[
        str | None, typer.Option("--scope-ref", help="Tenant id or workload name.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Halt the fleet in incident mode; drains triggers and broadcasts owners."""
    payload: dict[str, Any] = {
        "actor": actor,
        "reason": reason,
        "scope": scope,
        "scope_ref": scope_ref,
        "trigger": "operator",
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/fleet/pause", payload)
    if status_code >= 400 or status_code == 0:
        _fail("fleet pause", status_code, body)
    record = json.loads(body)
    typer.secho(
        f"OK: fleet halted ({record['incident_id']}); "
        f"owners_notified={len(record['owners_notified'])}",
        fg=typer.colors.RED,
    )


@fleet_app.command("resume")
def fleet_resume(
    actor: Annotated[str, typer.Option("--actor", help="Attributed operator.")] = "cli",
    incident_id: Annotated[
        str | None, typer.Option("--incident-id", help="Specific incident to resume.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Lift the halt with attribution and record the incident recovery."""
    payload: dict[str, Any] = {"actor": actor, "incident_id": incident_id}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/fleet/resume", payload)
    if status_code >= 400 or status_code == 0:
        _fail("fleet resume", status_code, body)
    record = json.loads(body)
    typer.secho(
        f"OK: fleet resumed by {record['resumed_by']} at {record['resumed_at']}",
        fg=typer.colors.GREEN,
    )


@fleet_app.command("status")
def fleet_status(api_url: ApiUrl = "http://localhost:8100") -> None:
    """Show whether the fleet is halted and the incident history."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/fleet/state")
    if status_code >= 400 or status_code == 0:
        _fail("fleet status", status_code, body)
    state = json.loads(body)
    if state["halted"] and state["active"] is not None:
        active = state["active"]
        typer.secho(
            f"HALTED: {active['incident_id']} scope={active['scope']} "
            f"by={active['actor']} at={active['halted_at']}",
            fg=typer.colors.RED,
        )
    else:
        typer.secho("RUNNING: no active incident", fg=typer.colors.GREEN)
    for record in state["history"]:
        state_label = "active" if record["resumed_at"] is None else "resolved"
        typer.echo(
            f"  {record['incident_id']}  {record['scope']}  {state_label}  by={record['actor']}"
        )


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Natural-language question.")],
    api_url: ApiUrl = "http://localhost:8100",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the raw answer as JSON.")
    ] = False,
) -> None:
    """Ask a live-state question; read-only and attributed to the caller."""
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/ask", {"question": question})
    if status_code >= 400 or status_code == 0:
        _fail("ask", status_code, body)
    answer = json.loads(body)
    if json_output:
        typer.echo(json.dumps(answer, indent=2))
        return
    typer.echo(answer["answer"])
    if answer.get("requires_confirmation"):
        typer.secho(
            "read-only: confirm a mutating action explicitly",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command()
def report(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    period: Annotated[str, typer.Option("--period", help="day|week|month")] = "week",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Render a fleet digest over health, cost, ROI, and approvals (read-only)."""
    query = urlencode({"tenant_id": tenant_id, "period": period})
    base = api_url.rstrip("/")
    endpoints = {
        "health": f"{base}/health",
        "cost": f"{base}/cost/showback?{query}",
        "roi": f"{base}/cost/roi/fleet?{query}",
        "approvals": f"{base}/analytics/approvals",
    }
    sections: dict[str, Any] = {}
    failures: list[str] = []
    for name, url in endpoints.items():
        status_code, body = _request("GET", url)
        if status_code == 0 or status_code >= 400:
            failures.append(f"{name} ({status_code})")
            typer.secho(
                f"report: {name} unavailable ({status_code})",
                fg=typer.colors.YELLOW,
                err=True,
            )
            continue
        sections[name] = json.loads(body)
    typer.echo(json.dumps(sections, indent=2))
    if failures and not sections:
        _fail("report", 0, f"all sections unavailable: {', '.join(failures)}")


@app.command()
def top(
    tenant_id: Annotated[str, typer.Option("--tenant")] = "default",
    period: Annotated[str, typer.Option("--period", help="day|week|month")] = "week",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Rank agents by ROI, flagging expensive-but-low-value workloads."""
    query = urlencode({"tenant_id": tenant_id, "period": period})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/cost/roi/fleet?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("top", status_code, body)
    report_body = json.loads(body)
    rows = sorted(report_body.get("rows", []), key=lambda row: row.get("roi", 0.0), reverse=True)
    for row in rows:
        flag = "FLAG" if row.get("expensive_low_value") else "ok"
        typer.echo(f"{row['workload_id']}  roi={row.get('roi', 0.0):.2f}  {flag}")


@app.command()
def logs(
    run_id: Annotated[str, typer.Argument(help="Run id to show the event log for.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Show a run's chronological event log (read-only)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/runs/{run_id}/events")
    if status_code >= 400 or status_code == 0:
        _fail("logs", status_code, body)
    for event in json.loads(body):
        typer.echo(
            f"{event.get('timestamp')}  {event.get('type')}  "
            f"{event.get('actor')}  {event.get('detail') or ''}"
        )


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="Run id to replay frame-by-frame.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Reconstruct a run frame-by-frame from its recorded history (no side effects)."""
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/replay/{run_id}")
    if status_code >= 400 or status_code == 0:
        _fail("replay", status_code, body)
    frameset = json.loads(body)
    frames = frameset["frames"]
    typer.secho(
        f"replaying {len(frames)} frames for run {run_id} (side-effect free)",
        fg=typer.colors.YELLOW,
    )
    for frame in frames:
        typer.echo(
            f"[{frame['sequence']}] {frame['event_type']}  {frame.get('from_state')} -> "
            f"{frame.get('to_state')}  {frame.get('detail') or ''}"
        )
    typer.echo(f"digest: {frameset['digest']}")


@app.command("replay-fork")
def replay_fork(
    run_id: Annotated[str, typer.Argument(help="Source run id to fork.")],
    edit: Annotated[
        list[str] | None,
        typer.Option("--edit", help="State edit as key=value (repeatable)."),
    ] = None,
    side_effects: Annotated[
        bool,
        typer.Option("--side-effects", help="Allow delivery and destructive tools."),
    ] = False,
    fork_point: Annotated[
        int | None,
        typer.Option("--fork-point", help="Frame sequence to fork from."),
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Fork a run: copy its task (with edits) into a new run and re-run it."""
    payload: dict[str, Any] = {
        "edits": _parse_edits(edit),
        "fork_point": fork_point,
        "side_effects": side_effects,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/runs/{run_id}/fork", payload)
    if status_code >= 400 or status_code == 0:
        _fail("fork", status_code, body)
    forked = json.loads(body)
    typer.secho(
        f"forked {run_id} -> {forked['forked_run_id']} (side_effects={forked['side_effects']})",
        fg=typer.colors.GREEN,
    )


@app.command("replay-diff")
def replay_diff(
    run_a: Annotated[str, typer.Argument(help="Before run id.")],
    run_b: Annotated[str, typer.Argument(help="After run id.")],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Compare two runs by state, calls, cost, and outcome."""
    query = urlencode({"run_a": run_a, "run_b": run_b})
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/replay/diff?{query}")
    if status_code >= 400 or status_code == 0:
        _fail("replay-diff", status_code, body)
    diff = json.loads(body)
    typer.secho(
        f"identical: {diff['identical']}  result_changed={diff['result_changed']}  "
        f"cost_delta={diff['cost_delta_usd']}",
        fg=typer.colors.YELLOW,
    )
    for delta in diff["field_deltas"]:
        typer.echo(f"{delta['field']}: {delta['before']} -> {delta['after']}")


@app.command("replay-ab")
def replay_ab(
    run_id: Annotated[str, typer.Argument(help="Source run id.")],
    workload_a: Annotated[str, typer.Option("--workload-a", help="First workload.")],
    workload_b: Annotated[str, typer.Option("--workload-b", help="Second workload.")],
    side_effects: Annotated[
        bool,
        typer.Option("--side-effects", help="Allow delivery and destructive tools."),
    ] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Run two workloads on identical input and print a side-by-side diff."""
    payload: dict[str, Any] = {
        "source_run_id": run_id,
        "workload_a": workload_a,
        "workload_b": workload_b,
        "side_effects": side_effects,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/replay/ab", payload)
    if status_code >= 400 or status_code == 0:
        _fail("replay-ab", status_code, body)
    result = json.loads(body)
    typer.secho(
        f"{result['workload_a']} -> {result['run_a_id']}  |  "
        f"{result['workload_b']} -> {result['run_b_id']}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"identical: {result['diff']['identical']}")


def _parse_edits(edit: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--edit key=value`` options into a mapping."""
    edits: dict[str, str] = {}
    for item in edit or []:
        key, _, value = item.partition("=")
        if not key:
            typer.secho(f"invalid edit {item!r}: expected key=value", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        edits[key] = value
    return edits


@artifacts_app.command("list")
def artifacts_list(
    run_id: Annotated[str | None, typer.Option("--run-id", help="Filter to one run.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """List stored artifacts (optionally for one run)."""
    query = urlencode({key: value for key, value in {"run_id": run_id}.items() if value})
    url = f"{api_url.rstrip('/')}/artifacts" + (f"?{query}" if query else "")
    status_code, body = _request("GET", url)
    if status_code >= 400 or status_code == 0:
        _fail("artifacts list", status_code, body)
    for artifact in json.loads(body):
        typer.echo(
            f"{artifact['artifact_id']}  {artifact['run_id']}  "
            f"{artifact['size_bytes']}B  {artifact['content_hash']}"
        )


@artifacts_app.command("show")
def artifacts_show(artifact_id: str, api_url: ApiUrl = "http://localhost:8100") -> None:
    """Show an artifact's metadata."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/artifacts/{artifact_id}")
    if status_code >= 400 or status_code == 0:
        _fail("artifacts show", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@artifacts_app.command("content")
def artifacts_content(
    artifact_id: str,
    out: Annotated[Path | None, typer.Option("--out", help="Write bytes to this file.")] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Fetch an artifact's bytes (to a file with --out, else stdout)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/artifacts/{artifact_id}/content")
    if status_code >= 400 or status_code == 0:
        _fail("artifacts content", status_code, body)
    if out is not None:
        out.write_bytes(body.encode("utf-8", "surrogateescape"))
        typer.secho(f"OK: wrote {out}", fg=typer.colors.GREEN)
        return
    typer.echo(body)


@artifacts_app.command("put")
def artifacts_put(
    run_id: Annotated[str, typer.Option("--run-id", help="Owning run id.")],
    path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="File to store."),
    ],
    retention_policy_id: Annotated[
        str | None, typer.Option("--retention", help="Retention policy id.")
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Store a file as a content-addressed run artifact."""
    payload: dict[str, Any] = {
        "run_id": run_id,
        "filename": path.name,
        "content": base64.b64encode(path.read_bytes()).decode("ascii"),
        "retention_policy_id": retention_policy_id,
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/artifacts", payload)
    if status_code >= 400 or status_code == 0:
        _fail("artifacts put", status_code, body)
    artifact = json.loads(body)
    typer.secho(
        f"OK: stored {artifact['artifact_id']} ({artifact['content_hash']})",
        fg=typer.colors.GREEN,
    )


@artifacts_app.command("purge")
def artifacts_purge(api_url: ApiUrl = "http://localhost:8100") -> None:
    """Purge expired artifacts, leaving audit evidence."""
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/retention/purge")
    if status_code >= 400 or status_code == 0:
        _fail("artifacts purge", status_code, body)
    result = json.loads(body)
    typer.echo(
        f"purged={len(result['purged'])} skipped_legal_hold={len(result['skipped_legal_hold'])}"
    )


@app.command("export")
def export_bundle(
    workload: Annotated[
        Path | None, typer.Option("--workload", exists=True, dir_okay=False)
    ] = None,
    corpus: Annotated[Path | None, typer.Option("--corpus", exists=True, dir_okay=False)] = None,
    policy_pack: Annotated[
        Path | None, typer.Option("--policy-pack", exists=True, dir_okay=False)
    ] = None,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Export a signed bundle of manifest + corpus + policy pack."""
    payload: dict[str, Any] = {}
    if workload is not None:
        payload["workload"] = _load_document(workload)
    if corpus is not None:
        payload["corpus"] = _load_document(corpus)
    if policy_pack is not None:
        payload["policy_pack"] = _load_document(policy_pack)
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/export", payload)
    if status_code >= 400 or status_code == 0:
        _fail("export", status_code, body)
    typer.echo(json.dumps(json.loads(body), indent=2))


@app.command("import")
def import_bundle(
    bundle: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Bundle JSON/YAML file."),
    ],
    allow_unsigned: Annotated[
        bool, typer.Option("--allow-unsigned", help="Accept an unsigned bundle.")
    ] = False,
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Verify a bundle and show the import plan (plan-only; never executes)."""
    payload = {"bundle": _load_document(bundle), "allow_unsigned": allow_unsigned}
    query = urlencode({"dry_run": "true"})
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/import?{query}", payload)
    if status_code >= 400 or status_code == 0:
        _fail("import", status_code, body)
    plan = json.loads(body)
    for entry in plan["entries"]:
        typer.echo(f"{entry['action']}  {entry['kind']}  {entry['name']}")
    typer.secho(
        "plan only: imported workloads register -> certify -> admit as usual",
        fg=typer.colors.YELLOW,
        err=True,
    )


@corpus_app.command("init")
def corpus_init(
    directory: Annotated[
        Path, typer.Argument(help="Directory to scaffold a corpus into.")
    ] = Path(),
    corpus_id: Annotated[
        str | None, typer.Option("--id", help="Corpus id (defaults to <dir>-corpus).")
    ] = None,
    template: Annotated[
        str,
        typer.Option(
            "--template",
            help="repo-agent|triage|generation|classification.",
        ),
    ] = "repo-agent",
    version: Annotated[int, typer.Option("--version", min=1)] = 1,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing corpus.")] = False,
) -> None:
    """Scaffold a starter benchmark corpus for a workload type."""
    try:
        kind = TemplateKind(template)
    except ValueError as exc:
        typer.secho(f"unknown template: {template}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    resolved_id = corpus_id or f"{directory.name or 'corpus'}-corpus"
    try:
        path = scaffold_corpus(
            directory,
            corpus_id=resolved_id,
            template=kind,
            version=version,
            force=force,
        )
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"OK: wrote {path}", fg=typer.colors.GREEN)
    for warning in lint_corpus(validate_corpus(path)):
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)


@corpus_app.command("validate")
def corpus_validate(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=True, readable=True)],
) -> None:
    """Validate a corpus file (or directory) against the strict schema."""
    try:
        corpus = validate_corpus(path)
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"OK: {corpus.id} v{corpus.version} ({len(corpus.tasks)} tasks)",
        fg=typer.colors.GREEN,
    )


@corpus_app.command("lint")
def corpus_lint(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=True, readable=True)],
) -> None:
    """Lint a valid corpus for authoring smells (critical/fast coverage)."""
    try:
        corpus = validate_corpus(path)
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    warnings = lint_corpus(corpus)
    if not warnings:
        typer.secho("OK: no lint warnings", fg=typer.colors.GREEN)
        return
    for warning in warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)


@corpus_app.command("add")
def corpus_add(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    task_id: Annotated[str, typer.Option("--task-id", help="Unique task id.")],
    name: Annotated[str, typer.Option("--name", help="Human task name.")],
    field: Annotated[str, typer.Option("--field", help="Output field to check.")],
    value: Annotated[str, typer.Option("--value", help="Expected exact value.")],
    critical: Annotated[
        bool, typer.Option("--critical/--no-critical", help="Gate on this task.")
    ] = True,
    fast: Annotated[bool, typer.Option("--fast", help="Include in the fast dev profile.")] = False,
) -> None:
    """Append an exact-match task to a corpus and rewrite it."""
    try:
        updated = add_task(
            path,
            task_id=task_id,
            name=name,
            field=field,
            value=value,
            critical=critical,
            fast=fast,
        )
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"OK: {updated.id} v{updated.version} now has {len(updated.tasks)} tasks",
        fg=typer.colors.GREEN,
    )


@corpus_app.command("publish")
def corpus_publish(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=True, readable=True)],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Validate a corpus and publish an immutable version to the plane."""
    try:
        corpus = validate_corpus(path)
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    payload = {"corpus": corpus.model_dump(mode="json", by_alias=True)}
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/corpora", payload)
    if status_code >= 400 or status_code == 0:
        _fail("corpus publish", status_code, body)
    release = json.loads(body)
    typer.secho(
        f"OK: published {release['corpus_id']} v{release['version']} "
        f"({release['content_hash'][:12]})",
        fg=typer.colors.GREEN,
    )


backup_app = typer.Typer(help="Control-plane backup and restore.", no_args_is_help=True)
app.add_typer(backup_app, name="backup")


@backup_app.command("create")
def backup_create(
    output: Annotated[Path, typer.Option("--output", help="Where to write the archive.")] = Path(
        "backup.json"
    ),
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Create a signed control-plane backup archive."""
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/backup")
    if status_code >= 400 or status_code == 0:
        _fail("backup create", status_code, body)
    output.write_text(body)
    archive = json.loads(body)
    typer.secho(
        f"OK: {archive['manifest']['backup_id']} "
        f"({archive['manifest']['total_records']} records) -> {output}",
        fg=typer.colors.GREEN,
    )


@backup_app.command("public-key")
def backup_public_key(
    output: Annotated[Path, typer.Option("--output", help="Where to write the PEM key.")] = Path(
        "backup.pub"
    ),
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Fetch and save the plane's backup verification key (PEM)."""
    status_code, body = _request("GET", f"{api_url.rstrip('/')}/backup/public-key")
    if status_code >= 400 or status_code == 0:
        _fail("backup public-key", status_code, body)
    output.write_text(json.loads(body)["public_key_pem"])
    typer.secho(f"OK: wrote {output}", fg=typer.colors.GREEN)


@backup_app.command("verify")
def backup_verify(
    archive_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    public_key: Annotated[
        Path, typer.Option("--public-key", exists=True, dir_okay=False, readable=True)
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Verify a backup archive against a public key."""
    payload = {
        "archive": json.loads(archive_file.read_text()),
        "public_key_pem": public_key.read_text(),
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/backup/verify", payload)
    if status_code >= 400 or status_code == 0:
        _fail("backup verify", status_code, body)
    if json.loads(body)["valid"]:
        typer.secho("OK: backup verifies", fg=typer.colors.GREEN)
    else:
        typer.secho("INVALID: backup failed verification", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@backup_app.command("restore")
def backup_restore(
    archive_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    public_key: Annotated[
        Path, typer.Option("--public-key", exists=True, dir_okay=False, readable=True)
    ],
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Restore control-plane state from a verified archive."""
    payload = {
        "archive": json.loads(archive_file.read_text()),
        "public_key_pem": public_key.read_text(),
    }
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/backup/restore", payload)
    if status_code >= 400 or status_code == 0:
        _fail("backup restore", status_code, body)
    for count in json.loads(body):
        typer.echo(f"{count['target']}: {count['restored']} restored")


demo_app = typer.Typer(help="Seed a demo fleet for screenshots and talks.", no_args_is_help=True)
app.add_typer(demo_app, name="demo")


@demo_app.command("seed")
def demo_seed(
    profile: Annotated[str, typer.Option("--profile", help="Demo profile name.")] = "default",
    api_url: ApiUrl = "http://localhost:8100",
) -> None:
    """Seed a screenshot-ready demo fleet (idempotent)."""
    status_code, body = _request("POST", f"{api_url.rstrip('/')}/demo/seed", {"profile": profile})
    if status_code >= 400 or status_code == 0:
        _fail("demo seed", status_code, body)
    report = json.loads(body)
    typer.secho(
        f"OK: seeded {report['workloads']} workloads, {report['runs']} runs "
        f"across {', '.join(report['tenant_ids'])}",
        fg=typer.colors.GREEN,
    )
