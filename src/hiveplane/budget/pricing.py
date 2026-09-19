"""Per-model token pricing (DD-04, D5)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.budget.errors import UnknownModelPriceError


class ModelPrice(BaseModel):
    """Token prices for a model, in USD per 1,000 tokens."""

    model_config = ConfigDict(extra="forbid")

    input_per_1k: float = Field(ge=0.0)
    output_per_1k: float = Field(ge=0.0)


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-4o/2024-08-06": ModelPrice(input_per_1k=0.005, output_per_1k=0.015),
    "openai/gpt-4o-mini/2024-07-18": ModelPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    "anthropic/claude-3-5-sonnet/20241022": ModelPrice(
        input_per_1k=0.003, output_per_1k=0.015
    ),
}

#: Identities under these prefixes incur no cost (local and fake providers).
ZERO_COST_PREFIXES: tuple[str, ...] = ("local/", "fake/")


class CostTable:
    """Maps exact model identities to prices and prices token usage."""

    def __init__(
        self,
        prices: dict[str, ModelPrice] | None = None,
        *,
        zero_cost_prefixes: tuple[str, ...] = ZERO_COST_PREFIXES,
    ) -> None:
        self._prices = dict(DEFAULT_PRICES)
        if prices is not None:
            self._prices.update(prices)
        self._zero_cost_prefixes = zero_cost_prefixes

    def price(self, model_identity: str, input_tokens: int, output_tokens: int) -> float:
        """Return the USD cost of token usage, raising for unknown models."""
        if model_identity.startswith(self._zero_cost_prefixes):
            return 0.0
        price = self._prices.get(model_identity)
        if price is None:
            raise UnknownModelPriceError(model_identity)
        return (input_tokens / 1000.0) * price.input_per_1k + (
            output_tokens / 1000.0
        ) * price.output_per_1k
