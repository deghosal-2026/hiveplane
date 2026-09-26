"""Progressive-delivery domain errors (M37, M38)."""

from __future__ import annotations


class ProgressiveError(Exception):
    """Base class for progressive-delivery errors."""


class ShadowNotFoundError(ProgressiveError):
    """Raised when a shadow run does not exist (or is out of scope)."""

    def __init__(self, shadow_run_id: str) -> None:
        super().__init__(f"shadow run {shadow_run_id!r} not found")
        self.shadow_run_id = shadow_run_id


class ShadowBudgetExceededError(ProgressiveError):
    """Raised when the shadow budget cap is already exhausted."""

    def __init__(self, budget_id: str) -> None:
        super().__init__(f"shadow budget {budget_id!r} is exhausted")
        self.budget_id = budget_id


class CanaryNotFoundError(ProgressiveError):
    """Raised when a canary rollout does not exist (or is out of scope)."""

    def __init__(self, rollout_id: str) -> None:
        super().__init__(f"canary rollout {rollout_id!r} not found")
        self.rollout_id = rollout_id


class CanaryNotAllowedError(ProgressiveError):
    """Raised when a canary transition is not permitted."""


class ExperimentNotFoundError(ProgressiveError):
    """Raised when an experiment campaign does not exist (or is out of scope)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"experiment campaign {campaign_id!r} not found")
        self.campaign_id = campaign_id
