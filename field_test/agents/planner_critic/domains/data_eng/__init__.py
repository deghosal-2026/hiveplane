"""Data engineering domain pack."""

from .gates import (
    DATA_ENG_CRITIC_PROMPT,
    DATA_ENG_PRECONDITIONS,
    DualWriteGate,
    SchemaPreVerificationGate,
    SLAWindowGate,
)


class DataEngineeringDomainPack:
    """Protocol-compliant domain pack for data engineering / DB reliability."""

    def __init__(self) -> None:
        self.name = "data_eng"
        self.gate_evaluators = [
            SchemaPreVerificationGate(),
            SLAWindowGate(),
            DualWriteGate(),
        ]
        self.precondition_catalog = DATA_ENG_PRECONDITIONS
        self.critic_prompt_template = DATA_ENG_CRITIC_PROMPT
        self.pack_config: dict[str, object] = {}
