from __future__ import annotations

import pytest

from hiveplane.budget.pricing import DEFAULT_PRICES, CostTable, ModelPrice
from hiveplane.config import Settings


def test_budget_settings_default_to_no_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIVEPLANE_BUDGET__PRICES", raising=False)
    monkeypatch.delenv("HIVEPLANE_BUDGET__ZERO_COST_PREFIXES", raising=False)

    settings = Settings()

    assert settings.budget.prices == {}
    assert settings.budget.zero_cost_prefixes is None


def test_budget_settings_parse_price_and_prefix_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HIVEPLANE_BUDGET__PRICES",
        '{"omlx/x/1": {"input_per_1k": 150.0, "output_per_1k": 600.0}}',
    )
    monkeypatch.setenv("HIVEPLANE_BUDGET__ZERO_COST_PREFIXES", "[]")

    settings = Settings()

    assert settings.budget.prices["omlx/x/1"] == ModelPrice(
        input_per_1k=150.0, output_per_1k=600.0
    )
    assert settings.budget.zero_cost_prefixes == []


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_BUDGET__PRICES", "")
    monkeypatch.setenv("HIVEPLANE_BUDGET__ZERO_COST_PREFIXES", "")

    settings = Settings()

    assert settings.budget.prices == {}
    assert settings.budget.zero_cost_prefixes is None


def test_cost_table_prices_an_override_and_drops_prefix_exemption() -> None:
    table = CostTable(
        {"omlx/x/1": ModelPrice(input_per_1k=150.0, output_per_1k=600.0)},
        zero_cost_prefixes=(),
    )

    assert table.price("omlx/x/1", 1000, 1000) == pytest.approx(750.0)
    # Without the prefix exemption, an unpriced local identity is an error.
    with pytest.raises(Exception):
        table.price("omlx/unknown/1", 1, 1)


def test_default_cost_table_keeps_local_free() -> None:
    table = CostTable()
    assert table.price("omlx/anything/1", 5000, 5000) == 0.0
    assert "openai/gpt-4o/2024-08-06" in DEFAULT_PRICES