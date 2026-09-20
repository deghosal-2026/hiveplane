"""SecOps domain pack."""

from .gates import (
    SECOPS_CRITIC_PROMPT,
    SECOPS_PRECONDITIONS,
    BlastRadiusGate,
    ForensicOrderGate,
    LeastPrivilegeGate,
)


class SecOpsDomainPack:
    """Protocol-compliant domain pack for security operations."""

    def __init__(self) -> None:
        self.name = "secops"
        self.gate_evaluators = [
            BlastRadiusGate(),
            ForensicOrderGate(),
            LeastPrivilegeGate(),
        ]
        self.precondition_catalog = SECOPS_PRECONDITIONS
        self.critic_prompt_template = SECOPS_CRITIC_PROMPT
        self.pack_config: dict[str, object] = {}
