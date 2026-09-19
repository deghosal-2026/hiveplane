"""Tests for the model cost table."""

from __future__ import annotations

import pytest

from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import DEFAULT_PRICES, CostTable, ModelPrice


def test_known_tokens_map_to_expected_cost() -> None:
    table = CostTable()
    cost = table.price("openai/gpt-4o/2024-08-06", input_tokens=1000, output_tokens=1000)
    assert cost == pytest.approx(0.005 + 0.015)


def test_unknown_model_fails_loudly() -> None:
    with pytest.raises(UnknownModelPriceError):
        CostTable().price("acme/mystery/1", input_tokens=1, output_tokens=1)


def test_custom_table_overrides_defaults() -> None:
    table = CostTable(prices={"acme/mystery/1": ModelPrice(input_per_1k=1.0, output_per_1k=2.0)})
    assert table.price("acme/mystery/1", 1000, 500) == pytest.approx(2.0)
    assert "openai/gpt-4o/2024-08-06" in DEFAULT_PRICES


def test_zero_tokens_cost_nothing() -> None:
    assert CostTable().price("openai/gpt-4o/2024-08-06", 0, 0) == 0.0


def test_local_and_fake_models_price_at_zero() -> None:
    table = CostTable()

    assert table.price("local/qwen2.5/7b", input_tokens=1000, output_tokens=500) == 0.0
    assert table.price("fake/echo/1", input_tokens=1000, output_tokens=500) == 0.0


def test_unknown_non_local_model_still_fails_loudly() -> None:
    with pytest.raises(UnknownModelPriceError):
        CostTable().price("acme/mystery/1", input_tokens=1, output_tokens=1)
