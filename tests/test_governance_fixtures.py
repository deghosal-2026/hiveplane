"""Governance scenario fixtures for the docker field test (M23, #93).

These are the seeded inputs for L5: an over-budget usage report (S5), a
destructive tool call (S6), and an oversized tool output (S7). The schemas here
are the contract the docker seeding helpers read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from hiveplane.budget.pricing import CostTable
from hiveplane.core.decision import ActionClass

_GOVERNANCE_DIR = (
    Path(__file__).resolve().parents[1] / "deploy" / "testdata" / "governance"
)


class _Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int
    model_identity: str


class _OverBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["over-budget"]
    workload: str
    context: Literal["sandbox", "staging", "production"]
    per_run_usd: float
    usage: _Usage


class _DestructiveCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["destructive-call"]
    workload: str
    tool_id: str
    action_class: Literal["destructive"]
    require_approval: bool
    arguments: dict[str, str]


class _LargeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["large-output"]
    workload: str
    tool_id: str
    max_bytes: int
    output_bytes: int


def _load(name: str) -> object:
    return json.loads((_GOVERNANCE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_over_budget_fixture_exceeds_its_per_run_budget() -> None:
    fixture = _OverBudget.model_validate(_load("over-budget"))

    cost = CostTable().price(
        fixture.usage.model_identity,
        fixture.usage.input_tokens,
        fixture.usage.output_tokens,
    )

    assert cost > fixture.per_run_usd, "seeded usage must exceed the run budget"


def test_destructive_call_fixture_is_destructive_and_approval_gated() -> None:
    fixture = _DestructiveCall.model_validate(_load("destructive-call"))

    assert fixture.action_class == ActionClass.DESTRUCTIVE.value
    assert fixture.require_approval is True


def test_large_output_fixture_exceeds_max_bytes() -> None:
    fixture = _LargeOutput.model_validate(_load("large-output"))

    assert fixture.output_bytes > fixture.max_bytes
