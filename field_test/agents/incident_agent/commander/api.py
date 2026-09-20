"""Python API for ai-incident-commander (run_incident, run_simulation)."""

from __future__ import annotations

from incident_commander.config import Config
from incident_commander.graph import build_and_run
from incident_commander.models.output import IncidentResult


def run_incident(  # noqa: ANN401
    alert: object,
    logs: object = None,
    messages: object = None,
    github: object = None,
    runbooks: object = None,
    manual_events: object = None,
    config: Config | None = None,
    output_dir: str | None = None,
    auto_approve: bool = False,
    thread_id: str | None = None,
) -> IncidentResult:
    """Execute the full incident-response graph against real input data.

    Accepts Alert objects, dicts, or file paths for all inputs (via normalizer).
    The normalizer converts each input to the appropriate Pydantic model
    before passing to the graph.
    """
    from incident_commander.ingest.normalizer import normalize

    cfg = config or Config()
    if auto_approve:
        cfg.mode = "simulate"  # bypasses human-in-the-loop gates in graph nodes

    # Normalize all input channels to Pydantic models
    # Each arg can be a model instance, raw dict, or file path string — the
    # normalizer dispatches on type at runtime.
    normalized = normalize(
        alert=alert,  # type: ignore[arg-type]
        logs=logs,  # type: ignore[arg-type]
        messages=messages,  # type: ignore[arg-type]
        github=github,  # type: ignore[arg-type]
        runbooks=runbooks,  # type: ignore[arg-type]
    )
    # manual_events appended separately because the normalizer only handles
    # the five canonical input channels above.
    normalized["manual_events"] = manual_events or []

    result = build_and_run(
        config=cfg,
        auto_approve=auto_approve,
        input_data=normalized,
    )

    # Use .get() with typed defaults so the IncidentResult is always valid
    # even if the graph exits early or a node fails to produce output.
    incident_result = IncidentResult(
        thread_id=thread_id or result.get("thread_id", ""),
        timeline=result.get("timeline", []),
        stakeholder_updates=result.get("stakeholder_updates", []),
        remediation_suggestions=result.get("remediation_suggestions", []),
        deploy_correlations=result.get("deploy_correlations", []),
        postmortem=result.get("postmortem"),
        cost_report=result.get("cost_report"),
        session_dir=output_dir or "",
    )

    # Writer imported lazily to avoid circular dependency at module level
    if output_dir:
        from incident_commander.output.markdown_writer import MarkdownOutputWriter
        writer = MarkdownOutputWriter(output_dir)
        writer.write_all(incident_result)

    return incident_result


def run_simulation(
    service: str = "payment-service",
    severity: str = "SEV1",
    scenario: str | None = None,
    seed: int = 42,
    config: Config | None = None,
    output_dir: str | None = None,
    auto_approve: bool = False,
) -> IncidentResult:
    """Run a bundled simulation scenario through the graph for demos/testing.

    Uses the built-in ``IncidentSimulator`` with the specified service and
    severity.  No real input data required — ideal for demos and CI.
    """
    cfg = config or Config()
    if auto_approve:
        cfg.mode = "simulate"

    # Two simulation paths: named scenario (deterministic script) vs
    # procedural generator (synthetic data seeded for reproducibility).
    if scenario:
        from incident_commander.simulation.scenarios import load_scenario
        incident_input = load_scenario(scenario, seed=seed)
    else:
        from incident_commander.simulation.simulator import IncidentSimulator
        sim = IncidentSimulator(seed=seed)
        incident_input = sim.simulate(
            service=service,
            severity=severity,
        )

    # Manually flatten the IncidentInput object into the dict shape that
    # build_and_run expects — avoids coupling the API to the state model.
    input_data = {
        "alert": incident_input.alert,
        "logs": incident_input.logs,
        "messages": incident_input.messages,
        "github": incident_input.github,
        "runbooks": incident_input.runbooks,
        "manual_events": incident_input.manual_events,
    }

    result = build_and_run(
        config=cfg,
        auto_approve=auto_approve,
        input_data=input_data,
    )

    incident_result = IncidentResult(
        thread_id=result.get("thread_id", ""),
        timeline=result.get("timeline", []),
        stakeholder_updates=result.get("stakeholder_updates", []),
        remediation_suggestions=result.get("remediation_suggestions", []),
        deploy_correlations=result.get("deploy_correlations", []),
        postmortem=result.get("postmortem"),
        cost_report=result.get("cost_report"),
        session_dir=output_dir or "",
    )

    if output_dir:
        from incident_commander.output.markdown_writer import MarkdownOutputWriter
        writer = MarkdownOutputWriter(output_dir)
        writer.write_all(incident_result)

    return incident_result
