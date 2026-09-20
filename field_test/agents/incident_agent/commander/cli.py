"""CLI entry point for ai-incident-commander."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from incident_commander.config import Config
from incident_commander.schema import export_schemas as _export_schemas
from incident_commander.schema import validate_input


@click.group()
@click.version_option(version="0.1.0", prog_name="incident-commander")
def main() -> None:
    """ai-incident-commander v0.1.0 — AI-powered incident response."""


@main.command()
@click.option("--service", default="payment-service", help="Service name")
@click.option("--severity", type=click.Choice(["SEV1", "SEV2", "SEV3"]), default="SEV1")
@click.option("--scenario", default=None, help="Scenario name from SCENARIOS")
@click.option("--seed", type=int, default=42, help="Random seed for reproducibility")
@click.option("--output-dir", default=None, help="Output directory")
@click.option("--auto-approve", is_flag=True, help="Skip all approval gates")
def simulate(
    service: str,
    severity: str,
    scenario: str | None,
    seed: int,
    output_dir: str | None,
    auto_approve: bool,
) -> None:
    """Run a simulated incident through the full graph."""
    # mode="simulate" bypasses human-in-the-loop interrupt gates;
    # mode="run" requires approval at each interrupt node.
    config = Config(mode="simulate" if auto_approve else "run")
    if output_dir:
        config.session_dir = output_dir

    from incident_commander.api import run_simulation
    result = run_simulation(
        service=service,
        severity=severity,
        scenario=scenario,
        seed=seed,
        config=config,
        output_dir=output_dir,
        auto_approve=auto_approve,
    )
    click.echo(f"Simulation complete. Thread ID: {result.thread_id}")
    if output_dir:
        click.echo(f"Output written to: {output_dir}")


@main.command()
@click.option("--alert", default=None, help="Alert JSON file")
@click.option("--logs", default=None, help="Logs directory")
@click.option("--messages", default=None, help="Messages JSON file")
@click.option("--github", default=None, help="GitHub PRs JSON file")
@click.option("--input-dir", default=None, help="Input directory (overrides individual flags)")
@click.option("--output-dir", default=None, help="Output directory")
@click.option("--auto-approve", is_flag=True, help="Skip all approval gates")
def run(
    alert: str | None,
    logs: str | None,
    messages: str | None,
    github: str | None,
    input_dir: str | None,
    output_dir: str | None,
    auto_approve: bool,
) -> None:
    """Run the incident-response graph against real input data."""
    # --input-dir provides a structured directory layout for complex setups;
    # --alert alone is the minimal entry point for simple single-input runs.
    if not input_dir and not alert:
        click.echo("Error: Either --alert or --input-dir is required.", err=True)
        sys.exit(1)

    config = Config(mode="simulate" if auto_approve else "run")

    from incident_commander.api import run_incident as _api_run
    # Two input paths: InputDirLoader bundles files from a directory structure,
    # while the else branch normalizes individually-provided file paths.
    if input_dir:
        from incident_commander.ingest.input_dir import InputDirLoader
        loader = InputDirLoader(input_dir)
        incident_input = loader.load()
        result = _api_run(
            alert=incident_input.alert,
            logs=incident_input.logs,
            messages=incident_input.messages,
            github=incident_input.github,
            runbooks=incident_input.runbooks,
            manual_events=incident_input.manual_events,
            config=config,
            output_dir=output_dir,
            auto_approve=auto_approve,
        )
    else:
        from incident_commander.ingest.normalizer import normalize
        assert alert is not None  # guarded by the --alert/--input-dir check above
        normalized = normalize(
            alert=alert,
            logs=logs,
            messages=messages,
            github=github,
        )
        result = _api_run(
            alert=normalized["alert"],
            logs=normalized["logs"],
            messages=normalized["messages"],
            github=normalized["github"],
            config=config,
            output_dir=output_dir,
            auto_approve=auto_approve,
        )

    click.echo(f"Run complete. Thread ID: {result.thread_id}")
    if output_dir:
        click.echo(f"Output written to: {output_dir}")


@main.command()
@click.option("--thread", required=True, help="Session thread ID")
def timeline(thread: str) -> None:
    """Display the timeline for a session."""
    config = Config()
    from incident_commander.persistence import SessionManager
    mgr = SessionManager(config.session_dir)
    try:
        session = mgr.load_session(thread)
    except KeyError:
        click.echo(f"Session not found: {thread}", err=True)
        sys.exit(1)

    timeline_events = session.get("timeline", [])
    if not timeline_events:
        click.echo("No timeline events.")
        return

    # Annotate events that correlate to a deploy with a visual marker
    for e in timeline_events:
        marker = " [DEPLOY]" if e.get("deploy_correlation") else ""
        ts = e.get("timestamp", "")
        src = e.get("source", "")
        content = e.get("content", "")
        click.echo(f"[{ts}] [{src}] {content}{marker}")


@main.command()
@click.option("--thread", required=True, help="Session thread ID")
def postmortem(thread: str) -> None:
    """Display the postmortem for a session."""
    config = Config()
    from incident_commander.persistence import SessionManager
    mgr = SessionManager(config.session_dir)
    try:
        session = mgr.load_session(thread)
    except KeyError:
        click.echo(f"Session not found: {thread}", err=True)
        sys.exit(1)

    pm = session.get("postmortem")
    if not pm:
        click.echo("No postmortem generated.")
        return
    # Fall back to thread ID if the postmortem lacks an incident_id
    click.echo(f"# Postmortem: {pm.get('incident_id', thread)}")
    click.echo(f"Summary: {pm.get('summary', {}).get('content', 'N/A')}")


@main.command()
@click.option("--output-dir", default="./schemas", help="Output directory for schema files")
def export_schemas_cmd(output_dir: str) -> None:
    """Export all JSON Schemas to files."""
    paths = _export_schemas(output_dir)
    click.echo(f"Exported {len(paths)} schemas to {output_dir}/")


@main.command()
@click.option("--alert", required=True, help="Alert JSON file to validate")
def validate(alert: str) -> None:
    """Validate an alert JSON file against the Alert schema."""
    import json
    path = Path(alert)
    if not path.exists():
        click.echo(f"File not found: {alert}", err=True)
        sys.exit(1)
    data = json.loads(path.read_text())
    # validate_input calls model_validate under the hood, raising
    # pydantic.ValidationError on mismatch; we only get here on success.
    result = validate_input(data, "alert")
    if result:
        click.echo("Alert validation: ✅ PASSED")
    else:
        click.echo("Alert validation: ❌ FAILED")
        sys.exit(1)
