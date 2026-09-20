"""JSON Schema registry, validation, and export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import (
    Alert,
    ChatMessage,
    CostReport,
    DeployCorrelation,
    GitHubPR,
    IncidentInput,
    IncidentMeta,
    IncidentResult,
    LLMCall,
    LogEntry,
    Postmortem,
    RemediationSuggestion,
    Runbook,
    SessionMeta,
    StakeholderUpdate,
    TimelineEvent,
)

# Bump on breaking schema changes; used for compatibility checks
SCHEMA_VERSION = "0.1.0"

# Maps kebab-case schema names to Pydantic model classes.
# This registry drives both JSON Schema export and runtime validation
# without requiring callers to import model classes directly.
SCHEMAS: dict[str, type[BaseModel]] = {
    "alert": Alert,
    "chat-message": ChatMessage,
    "log-entry": LogEntry,
    "github-pr": GitHubPR,
    "runbook": Runbook,
    "incident-meta": IncidentMeta,
    "incident-input": IncidentInput,
    "incident-result": IncidentResult,
    "timeline-event": TimelineEvent,
    "deploy-correlation": DeployCorrelation,
    "stakeholder-update": StakeholderUpdate,
    "remediation-suggestion": RemediationSuggestion,
    "postmortem": Postmortem,
    "cost-report": CostReport,
    "session-meta": SessionMeta,
    "llm-call": LLMCall,
}


def export_schemas(output_dir: str | Path) -> list[Path]:
    """Export all JSON Schemas to individual files in the output directory."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    exported: list[Path] = []
    for name, model_cls in SCHEMAS.items():
        schema = model_cls.model_json_schema()
        # Inject $id as the canonical schema identifier for tooling resolution
        schema["$id"] = f"https://schemas.incident-commander.dev/{name}.json"
        # Declare the JSON Schema draft version for external validators
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"

        file_path = output_path / f"{name}.json"
        file_path.write_text(
            # default=str serializes datetime and other non-JSON types to strings,
            # producing valid JSON even when the schema contains Python-specific types.
            json.dumps(schema, indent=2, default=str)
        )
        exported.append(file_path)

    return exported


def validate_input(data: dict[str, Any], schema_name: str) -> bool:
    """Validate a dict against a named schema.

    Returns True if valid, raises ValidationError if invalid.
    """
    if schema_name not in SCHEMAS:
        # Reject unknown schema names with the list of available ones
        raise KeyError(
            f"Unknown schema: {schema_name}. "
            f"Available: {list(SCHEMAS.keys())}"
        )

    model_cls = SCHEMAS[schema_name]
    # Raises pydantic.ValidationError if data does not match the schema
    model_cls.model_validate(data)
    return True
