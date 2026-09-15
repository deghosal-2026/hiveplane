"""Tests that the bundled example workloads validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.core.manifest import load_manifest

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"

EXPECTED = {
    "repo-agent": "raw-worker",
    "docs-agent": "langgraph",
    "incident-agent": "raw-worker",
}


@pytest.mark.parametrize(("name", "adapter"), sorted(EXPECTED.items()))
def test_example_workload_validates(name: str, adapter: str) -> None:
    workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")

    assert workload.name == name
    assert workload.spec.runtime.adapter.value == adapter
    assert workload.spec.budget.per_run_usd > 0
    assert workload.spec.tools is not None
    assert workload.certification_status.value == "uncertified"


def test_every_example_covers_the_required_blocks() -> None:
    for name in EXPECTED:
        workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")
        spec = workload.spec

        assert spec.runtime is not None, name
        assert spec.budget is not None, name
        assert spec.model.identity is not None, name
        assert spec.certification is not None, name
        assert spec.tools.allow or spec.tools.deny, name
        assert spec.approvals is not None, name
        assert spec.triggers, name
        assert spec.sandbox is not None, name
        assert spec.fan_out is not None, name
