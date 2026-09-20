"""Adapter contract and shared helpers.

Every adapter follows the same contract: given a
:class:`~evalforge.models.pack.Scenario` and a config dict, invoke the agent
and return a normalized :class:`~evalforge.models.artifact.RunArtifact`. This
module owns the three shared pieces of that pipeline:

1. :func:`build_invocation_payload` -- the restricted payload sent to agents.
   Evaluation-only fields (``expected``, ``metrics``) are **never** included
   (spec §"Agent Invocation Payload"); this is the ground-truth-leakage
   guarantee.
2. :func:`parse_agent_stdout` -- parses the agent's stdout as a JSON envelope
   (``evalforge.run_envelope.v1``) with a raw-text fallback unless
   ``strict_output`` is set.
3. :class:`Adapter` -- the abstract base that turns an invocation result into a
   ``RunArtifact``, capturing timing, cost, trajectory, and error/status
   normalization so concrete adapters only implement ``_invoke``.
"""

from __future__ import annotations

import importlib.metadata
import json
from abc import ABC, abstractmethod
from typing import Any, Literal

from evalforge.errors import AdapterErrorTaxonomy
from evalforge.models.adapter_manifest import AdapterManifest
from evalforge.models.artifact import Cost, RunArtifact, RunOutput, RunTimestamps, TrajectoryStep
from evalforge.models.errors import AdapterError, AgentTimeoutError
from evalforge.models.pack import Scenario
from evalforge.models.trace import compute_trace_diff

INVOCATION_SCHEMA_VERSION = "evalforge.invocation_payload.v1"
RUN_ENVELOPE_SCHEMA_VERSION = "evalforge.run_envelope.v1"


def build_invocation_payload(scenario: Scenario, run_id: str) -> dict[str, Any]:
    """Build the restricted payload sent to an agent.

    Evaluation-only fields (``expected``, ``metrics``) are never included, so
    agents cannot game or memorize ground truth. Tools are sent as ``ToolSpec``
    objects (name + description), not bare strings, per the spec.

    Args:
        scenario: The scenario to build the payload from.
        run_id: Unique identifier for this run.

    Returns:
        A dict containing the invocation payload with schema version, run/scenario
        IDs, input, context, allowed/disallowed tools, and budget.
    """
    return {
        "schema_version": INVOCATION_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario_id": scenario.id,
        "input": scenario.input,
        "context": scenario.context,
        "allowed_tools": [tool.model_dump() for tool in scenario.allowed_tools],
        "disallowed_tools": [tool.model_dump() for tool in scenario.disallowed_tools],
        "budget": scenario.budget.model_dump() if scenario.budget else {},
    }


def parse_agent_stdout(stdout: str, *, strict: bool = False) -> dict[str, Any]:
    """Parse agent stdout as a JSON envelope, falling back to raw text.

    When ``strict`` is true, non-JSON output raises
    :class:`~evalforge.models.errors.AdapterError` instead of being treated as
    a plain-text final answer.

    Args:
        stdout: The raw stdout string from the agent.
        strict: If True, require valid JSON output; otherwise fall back to
            treating plain text as a completed run with raw-text output.

    Returns:
        A run envelope dict, either parsed from JSON or built from raw text.

    Raises:
        AdapterError: If strict mode is enabled and stdout is not valid JSON
            or not a JSON object.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        if strict:
            raise AdapterError("agent stdout is not valid JSON (strict_output=true)") from None
        return _raw_text_envelope(stdout)
    if not isinstance(data, dict):
        if strict:
            raise AdapterError("agent stdout is not a JSON object (strict_output=true)") from None
        return _raw_text_envelope(stdout)
    return data


def _raw_text_envelope(stdout: str) -> dict[str, Any]:
    """Build a run envelope from plain text, stripping terminal whitespace.

    Args:
        stdout: The raw stdout string.

    Returns:
        A run envelope dict with status "completed" and the raw text as the
        final output.
    """
    return {
        "schema_version": RUN_ENVELOPE_SCHEMA_VERSION,
        "status": "completed",
        "output": {"final": stdout.rstrip(), "structured": None},
        "trajectory": {"steps": []},
        "cost": None,
        "error": None,
    }


class Adapter(ABC):
    """Base class for agent adapters.

    Subclasses implement :meth:`_invoke`, which performs the actual agent
    invocation and returns either raw stdout (``str``) or an already-parsed
    envelope (``dict``). :meth:`run` handles payload building, timing,
    error/status normalization, and artifact construction.

    Attributes:
        name: A string identifier for the adapter type (e.g. "subprocess",
            "http", "langgraph").
    """

    name: str

    def run(self, scenario: Scenario, config: dict[str, Any]) -> RunArtifact:
        """Invoke the agent for a scenario and return a normalized RunArtifact.

        This method orchestrates the full lifecycle: payload building,
        invocation via _invoke, stdout parsing, timing capture, error
        normalization, and artifact construction.

        Args:
            scenario: The scenario to evaluate.
            config: Adapter configuration dict (may include run_id,
                strict_output, timeout_seconds, etc.).

        Returns:
            A RunArtifact with normalized status, output, trajectory, cost,
            and error information.
        """
        run_id = config.get("run_id", "run-unknown")
        payload = build_invocation_payload(scenario, run_id)
        start_iso = _now_iso()
        start_ms = _now_ms()
        strict = bool(config.get("strict_output", False))
        try:
            raw = self._invoke(payload, config)
            if isinstance(raw, str):
                envelope = parse_agent_stdout(raw, strict=strict)
            elif isinstance(raw, dict):
                envelope = raw
            else:
                raise AdapterError(f"adapter returned unexpected type: {type(raw).__name__}")
        except AgentTimeoutError as exc:
            return _artifact_for_error(
                scenario, run_id, "timeout", str(exc), config, start_iso, start_ms
            )
        except AdapterError as exc:
            return _artifact_for_error(
                scenario, run_id, "error", str(exc), config, start_iso, start_ms
            )
        except Exception as exc:
            return _artifact_for_error(
                scenario, run_id, "error", str(exc), config, start_iso, start_ms
            )

        try:
            return _artifact_from_envelope(envelope, scenario, run_id, config, start_iso, start_ms)
        except Exception as exc:
            return _artifact_for_error(
                scenario,
                run_id,
                "error",
                f"invalid agent envelope: {exc}",
                config,
                start_iso,
                start_ms,
            )

    def get_manifest(self) -> dict[str, Any]:
        """Return the adapter manifest as a serialised dict.

        Returns a dict with adapter identity, capabilities, schema contracts,
        and a SHA-256 digest computed from all fields. Subclasses should
        override to provide adapter-specific values.
        """
        try:
            version = importlib.metadata.version("agent-eval-forge")
        except (importlib.metadata.PackageNotFoundError, OSError):
            version = "0.0.0"
        manifest = AdapterManifest(
            name=self.name,
            version=version,
            capabilities=["stdin_stdout"],
            input_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "timeout_seconds": {"type": "number", "default": 120},
                    "strict_output": {"type": "boolean", "default": False},
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "status": {"type": "string"},
                    "output": {"type": "object"},
                    "trajectory": {"type": "object"},
                    "cost": {"type": "object"},
                    "error": {"type": "string"},
                },
            },
            tool_event_stream_version=RUN_ENVELOPE_SCHEMA_VERSION,
            network_policy="allow_none",
        )
        return manifest.to_dict()

    @abstractmethod
    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str | dict[str, Any]:
        """Invoke the agent; return raw stdout (str) or an envelope dict.

        Args:
            payload: The invocation payload dict.
            config: Adapter configuration dict.

        Returns:
            Either a raw stdout string or an already-parsed run envelope dict.

        Raises:
            AdapterError: On invocation failure.
            AgentTimeoutError: On timeout.
        """


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _now_ms() -> int:
    """Return the current time in milliseconds since the epoch."""
    import time

    return int(time.time() * 1000)


def _artifact_for_error(
    scenario: Scenario,
    run_id: str,
    status: Literal["timeout", "error"],
    error: str,
    config: dict[str, Any],
    start_iso: str,
    start_ms: int,
    *,
    trace_diff: dict[str, Any] | None = None,
) -> RunArtifact:
    """Build a RunArtifact representing a failed run.

    Args:
        scenario: The scenario being evaluated.
        run_id: Unique run identifier.
        status: Error status string ("error" or "timeout").
        error: Human-readable error description.
        config: Adapter configuration (used for agent metadata and type).
        start_iso: ISO-8601 start timestamp.
        start_ms: Start time in milliseconds.
        trace_diff: Optional pre-computed trace-diff dict. When ``None``, trace
            diff is derived automatically from status.

    Returns:
        A RunArtifact with the error status, zeroed output/trajectory/cost,
        and a normalized error category.
    """
    end_ms = _now_ms()
    adapter_type = config.get("type", "unknown")
    normalized = AdapterErrorTaxonomy.normalize(Exception(error), adapter_type)
    if trace_diff is None:
        trace_diff = compute_trace_diff(
            artifact_has_trajectory=False,
            artifact_status=status,
        )
    return RunArtifact(
        id=f"{run_id}-{scenario.id}",
        scenario_id=scenario.id,
        agent=_sanitize_agent(config),
        timestamp=RunTimestamps(start=start_iso, end=_now_iso(), duration_ms=end_ms - start_ms),
        output=RunOutput(final=None, structured=None),
        trajectory=[],
        cost=Cost(),
        status=status,
        error=error,
        error_category=normalized["category"],
        trace_diff=trace_diff,
    )


def _artifact_from_envelope(
    envelope: dict[str, Any],
    scenario: Scenario,
    run_id: str,
    config: dict[str, Any],
    start_iso: str,
    start_ms: int,
) -> RunArtifact:
    """Build a RunArtifact from a successful agent run envelope.

    Args:
        envelope: The run envelope dict returned by the agent.
        scenario: The scenario being evaluated.
        run_id: Unique run identifier.
        config: Adapter configuration.
        start_iso: ISO-8601 start timestamp.
        start_ms: Start time in milliseconds.

    Returns:
        A RunArtifact with parsed output, trajectory, cost, and status.

    Note:
        A "completed" run that produced no output at all is treated as an
        error -- blank completions usually signal a dead entry point or
        empty tool result, and must never count as passes.
    """
    end_ms = _now_ms()
    status = envelope.get("status", "completed")
    output = envelope.get("output") or {}
    final = output.get("final")
    structured = output.get("structured")
    trajectory_raw = (envelope.get("trajectory") or {}).get("steps") or []
    cost_raw = envelope.get("cost")
    steps: list[TrajectoryStep] = []
    for step in trajectory_raw:
        parsed = _step_or_skip(step)
        if parsed is not None:
            steps.append(parsed)

    if status == "completed" and not final and structured is None:
        return _artifact_for_error(
            scenario,
            run_id,
            "error",
            "agent returned no output (blank completion)",
            config,
            start_iso,
            start_ms,
        )

    return RunArtifact(
        id=f"{run_id}-{scenario.id}",
        scenario_id=scenario.id,
        agent=_sanitize_agent(config),
        timestamp=RunTimestamps(start=start_iso, end=_now_iso(), duration_ms=end_ms - start_ms),
        output=RunOutput(final=final, structured=structured),
        trajectory=steps,
        cost=Cost.model_validate(cost_raw) if isinstance(cost_raw, dict) else Cost(),
        status=status,
        error=envelope.get("error"),
    )


def _step_or_skip(step: Any) -> TrajectoryStep | None:
    """Coerce a raw trajectory step into a model, skipping malformed ones.

    Malformed steps (non-dict or validation failures) are silently dropped
    to prevent a single bad step from failing the entire run.

    Args:
        step: A raw trajectory step, expected to be a dict.

    Returns:
        A validated TrajectoryStep, or None if the step is malformed.
    """
    if not isinstance(step, dict):
        return None
    try:
        return TrajectoryStep.model_validate(step)
    except Exception:
        return None


def _sanitize_agent(config: dict[str, Any]) -> dict[str, Any]:
    """Copy config minus any secret-looking keys, for artifact provenance.

    Agent config is written into the run index and artifacts; API keys and
    tokens must never be persisted (spec §"Run Index").

    Args:
        config: The raw adapter configuration dict.

    Returns:
        A sanitized copy of config with secret keys removed.
    """
    secret_keys = {"api_key", "token", "secret", "password"}
    return {key: value for key, value in config.items() if key not in secret_keys}


def _inject_fixtures(payload: dict[str, Any], config: dict[str, Any]) -> None:
    """Stamp fixture metadata on the invocation payload.

    When fixture mode is enabled, the payload is annotated so the agent
    knows it can read fixture files from a known directory.

    Args:
        payload: The invocation payload dict (mutated in place).
        config: Adapter configuration dict.
    """
    if config.get("fixtures"):
        payload["_fixture_mode"] = True
        payload["_fixtures_dir"] = config.get("fixtures_dir", "scenarios/fixtures")
