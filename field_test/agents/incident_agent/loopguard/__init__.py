"""loopguard — Circuit breaker and escalation for LLM agent loops.

Public API: Guard, GuardConfig, TriggerConfig, FailureDetector, and the
exception hierarchy.

Integration imports are optional and require extras:
    from ai_loopguard.integrations.langgraph import LangGraphHandler  # ai-loopguard[langgraph]
    from ai_loopguard.integrations.crewai import CrewAIWrapper        # ai-loopguard[crewai]
    from ai_loopguard.integrations.otel import OTelEventHook          # ai-loopguard[otel]
"""

# Guard is the main user-facing class — wraps agent functions with @guard.protect.
# GuardConfig / TriggerConfig are Pydantic models; FailureDetector runs trigger checks.
from ai_loopguard.config import GuardConfig, TriggerConfig
from ai_loopguard.detectors import FailureDetector
from ai_loopguard.exceptions import (
    ConfigError,
    ContextError,
    EscalationCapError,
    EscalationError,
    LoopguardError,
    TriggerError,
)
from ai_loopguard.guard import Guard

__version__ = "0.1.0"

# Backwards-compatible alias matching SPEC §16 original naming
# (was "EscalationCapReached", renamed to "EscalationCapError" per ruff N818)
EscalationCapReached = EscalationCapError

# __all__ explicitly lists the public API surface. Integration imports
# (LangGraph, CrewAI, OTel) are NOT included — they require extras.
__all__ = [
    "Guard",
    "GuardConfig",
    "TriggerConfig",
    "FailureDetector",
    "LoopguardError",
    "EscalationError",
    "EscalationCapError",
    "EscalationCapReached",  # backwards-compat alias
    "TriggerError",
    "ConfigError",
    "ContextError",
]
