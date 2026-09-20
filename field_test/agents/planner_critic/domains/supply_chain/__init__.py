"""Supply-chain domain pack."""

from .gates import (
    SUPPLY_CHAIN_CRITIC_PROMPT,
    SUPPLY_CHAIN_PRECONDITIONS,
    ArtifactIntegrityGate,
    BreakingChangeGate,
    TransitiveLockingGate,
)


class SupplyChainDomainPack:
    """Protocol-compliant domain pack for software supply-chain."""

    def __init__(self) -> None:
        self.name = "supply_chain"
        self.gate_evaluators = [
            TransitiveLockingGate(),
            BreakingChangeGate(),
            ArtifactIntegrityGate(),
        ]
        self.precondition_catalog = SUPPLY_CHAIN_PRECONDITIONS
        self.critic_prompt_template = SUPPLY_CHAIN_CRITIC_PROMPT
        self.pack_config: dict[str, object] = {}
