"""Budget domain errors."""

from __future__ import annotations


class BudgetError(Exception):
    """Base class for budget errors."""


class UnknownModelPriceError(BudgetError):
    """Raised when a model identity has no price in the cost table."""

    def __init__(self, model_identity: str) -> None:
        super().__init__(f"no price configured for model {model_identity!r}")
        self.model_identity = model_identity


class MissingModelIdentityError(BudgetError):
    """Raised when usage cannot be priced because no model identity is present."""

    def __init__(self) -> None:
        super().__init__(
            "usage cannot be priced without a model identity; adapters must report the "
            "exact model identity on every usage event"
        )
