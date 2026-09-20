"""FinOps domain pack."""

from .gates import (
    FINOPS_CRITIC_PROMPT,
    FINOPS_PRECONDITIONS,
    BudgetBoundaryGate,
    GracePeriodGate,
)


class FinOpsDomainPack:
    """Protocol-compliant domain pack for FinOps / cost governance."""

    def __init__(self, budget_cap: float = 100_000) -> None:
        self.name = "finops"
        self.gate_evaluators = [
            GracePeriodGate(),
            BudgetBoundaryGate(budget_cap=budget_cap),
        ]
        self.precondition_catalog = FINOPS_PRECONDITIONS
        self.critic_prompt_template = FINOPS_CRITIC_PROMPT
        self.pack_config: dict[str, object] = {"budget_cap": budget_cap}
