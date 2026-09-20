"""Tool-result provenance and capability-scoped state transitions (#249, #258).

Tool results carry typed provenance (source channel, trust level, content hash)
and a capability level. Deterministic policy gates check whether a given source
channel may influence a given state transition, preventing indirect prompt
injection through tool outputs.

Capability levels (ordered, strict):
    untrusted_web < external_api < internal_db < internal_verified < admin_override

Each tool in the engine declares its source capability level (hardcoded, not
LLM-decided). Each state transition declares the minimum capability level
required. The capability gate blocks any transition where source capability
does not meet the required capability.
"""

from __future__ import annotations

from enum import IntEnum


class CapabilityLevel(IntEnum):
    """Ordered capability levels for tool results.

    Lower values = less trusted. A source with capability level X can
    influence transitions whose required level is <= X.
    """

    UNTRUSTED_WEB = 0
    EXTERNAL_API = 10
    INTERNAL_DB = 20
    INTERNAL_VERIFIED = 30
    ADMIN_OVERRIDE = 40

    def __str__(self) -> str:
        return self.name.lower()

    @classmethod
    def from_str(cls, s: str) -> CapabilityLevel:
        """Parse a capability level from its string representation."""
        mapping = {
            "untrusted_web": cls.UNTRUSTED_WEB,
            "external_api": cls.EXTERNAL_API,
            "internal_db": cls.INTERNAL_DB,
            "internal_verified": cls.INTERNAL_VERIFIED,
            "admin_override": cls.ADMIN_OVERRIDE,
        }
        return mapping[s.lower()]


# Minimum capability required for each transition type
TRANSITION_REQUIREMENTS: dict[str, CapabilityLevel] = {
    "discovery": CapabilityLevel.UNTRUSTED_WEB,
    "read": CapabilityLevel.INTERNAL_DB,
    "write": CapabilityLevel.INTERNAL_VERIFIED,
    "approve": CapabilityLevel.ADMIN_OVERRIDE,
    "deploy": CapabilityLevel.INTERNAL_VERIFIED,
    "rollback": CapabilityLevel.INTERNAL_VERIFIED,
    "secret_access": CapabilityLevel.ADMIN_OVERRIDE,
    "payment": CapabilityLevel.ADMIN_OVERRIDE,
    "identity_change": CapabilityLevel.ADMIN_OVERRIDE,
}


def check_transition_capability(
    source_capability: CapabilityLevel, transition: str
) -> tuple[bool, str]:
    """Check if a source capability meets the requirement for a transition.

    Args:
        source_capability: The capability level of the source.
        transition: The type of transition being attempted.

    Returns:
        ``(allowed, reason)`` — ``allowed`` is True when the source capability
        meets or exceeds the requirement for the transition.
    """
    required = TRANSITION_REQUIREMENTS.get(transition)
    if required is None:
        return True, f"unknown transition '{transition}' — allowed by default"
    if source_capability >= required:
        return True, (
            f"source capability {source_capability} >= "
            f"required {required} for transition '{transition}'"
        )
    return False, (
        f"source capability {source_capability} below required "
        f"{required} for transition '{transition}' — blocked"
    )


def default_capability_for_source(source: str) -> CapabilityLevel:
    """Determine the default capability level for a source channel.

    Args:
        source: The source channel identifier (e.g. 'web_fetch', 'db_query',
            'user_input', 'admin_api').

    Returns:
        The appropriate capability level.
    """
    mapping: dict[str, CapabilityLevel] = {
        "web_fetch": CapabilityLevel.UNTRUSTED_WEB,
        "external_api": CapabilityLevel.EXTERNAL_API,
        "db_query": CapabilityLevel.INTERNAL_DB,
        "internal_api": CapabilityLevel.INTERNAL_DB,
        "verified_internal": CapabilityLevel.INTERNAL_VERIFIED,
        "admin_api": CapabilityLevel.ADMIN_OVERRIDE,
        "user_input": CapabilityLevel.UNTRUSTED_WEB,
        "cli_input": CapabilityLevel.INTERNAL_DB,
        "file_read": CapabilityLevel.INTERNAL_DB,
        "env_var": CapabilityLevel.INTERNAL_DB,
        "secret_store": CapabilityLevel.ADMIN_OVERRIDE,
    }
    return mapping.get(source, CapabilityLevel.UNTRUSTED_WEB)


class ToolResultProvenance:
    """Provenance metadata for a tool result.

    Attributes:
        source: The source channel identifier.
        capability: The capability level of the source.
        content_hash: Optional hash of the result content for integrity.
    """

    def __init__(
        self,
        source: str,
        capability: CapabilityLevel | None = None,
        content_hash: str | None = None,
    ) -> None:
        self.source = source
        self.capability = capability or default_capability_for_source(source)
        self.content_hash = content_hash

    def can_perform(self, transition: str) -> bool:
        """Check if this provenance allows a transition.

        Args:
            transition: The transition type to check.

        Returns:
            True when the source capability meets the requirement.
        """
        allowed, _ = check_transition_capability(self.capability, transition)
        return allowed

    def check(self, transition: str) -> tuple[bool, str]:
        """Check if this provenance allows a transition, with reason.

        Args:
            transition: The transition type to check.

        Returns:
            ``(allowed, reason)`` tuple.
        """
        return check_transition_capability(self.capability, transition)


__all__ = [
    "TRANSITION_REQUIREMENTS",
    "CapabilityLevel",
    "ToolResultProvenance",
    "check_transition_capability",
    "default_capability_for_source",
]
