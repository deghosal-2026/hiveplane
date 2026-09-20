"""ai-incident-commander — AI-powered incident response automation."""

from __future__ import annotations

from incident_commander.api import run_incident, run_simulation
from incident_commander.config import Config, LLMConfig
from incident_commander.graph import build_graph
from incident_commander.models.input import IncidentInput, IncidentMeta, Runbook
from incident_commander.models.output import IncidentResult, LLMCall, SessionMeta

# Re-exporting all state models so consumers can construct or inspect
# graph state without importing from nested model subpackages.
from incident_commander.models.state import (
    ActionItem,
    Alert,
    ChatMessage,
    CostReport,
    DeployCorrelation,
    GitHubPR,
    IncidentState,
    LogEntry,
    NodeCost,
    Postmortem,
    PostmortemSection,
    RemediationSuggestion,
    StakeholderUpdate,
    TimelineEvent,
)
from incident_commander.nodes.rag import InMemoryRetriever
from incident_commander.schema import SCHEMAS, export_schemas, validate_input
from incident_commander.simulation.scenarios import SCENARIOS, load_scenario
from incident_commander.simulation.simulator import IncidentSimulator

__version__ = "0.1.0"

__all__ = [
    "run_incident",
    "run_simulation",
    "build_graph",
    "Config",
    "LLMConfig",
    "IncidentState",
    "IncidentInput",
    "IncidentResult",
    "IncidentMeta",
    "Alert",
    "ChatMessage",
    "LogEntry",
    "GitHubPR",
    "TimelineEvent",
    "DeployCorrelation",
    "StakeholderUpdate",
    "RemediationSuggestion",
    "Postmortem",
    "PostmortemSection",
    "ActionItem",
    "CostReport",
    "NodeCost",
    "LLMCall",
    "SessionMeta",
    "Runbook",
    "InMemoryRetriever",
    "SCENARIOS",
    "SCHEMAS",
    "load_scenario",
    "export_schemas",
    "validate_input",
    "IncidentSimulator",
]
