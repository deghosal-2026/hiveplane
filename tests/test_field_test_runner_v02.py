"""Tests for the v0.2.0 real-agent field-test runner (M61)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import field_test_runner_v02 as runner_module  # type: ignore[import-not-found]  # noqa: E402
from field_test_runner_v02 import (  # noqa: E402
    Api,
    Runner,
    ScenarioError,
)

from hiveplane.core.manifest import load_manifest  # noqa: E402


def test_workload_evidence_points_at_real_field_test_assets(tmp_path: Path) -> None:
    runner = Runner(Api("http://localhost:8100"), tmp_path)

    evidence = runner.workloads_evidence()

    assert evidence["workloads_root"] == "field_test/workloads"
    for info in evidence["workloads"].values():
        assert info["manifest"].startswith("field_test/workloads/")
        assert info["entrypoint"].startswith("field_test.shims.")


def test_real_field_test_workloads_reference_field_test_shims_and_corpora() -> None:
    workloads_dir = ROOT / "field_test" / "workloads"

    support = load_manifest(workloads_dir / "support-agent.yaml")
    judge = load_manifest(workloads_dir / "eval-judge.yaml")
    regressed = load_manifest(workloads_dir / "regressed-agent.yaml")

    assert support.spec.runtime.entrypoint == "field_test.shims.support_agent:run"
    assert judge.spec.runtime.entrypoint == "field_test.shims.eval_judge:graph"
    assert regressed.spec.runtime.entrypoint == "field_test.shims.regressed_agent:run"

    assert support.spec.certification is not None
    assert judge.spec.certification is not None
    assert regressed.spec.certification is not None

    assert (
        support.spec.certification.benchmark_corpus == "corpora/support-agent/hiveplane-corpus.yaml"
    )
    assert judge.spec.certification.benchmark_corpus == "corpora/eval-judge/hiveplane-corpus.yaml"
    assert (
        regressed.spec.certification.benchmark_corpus
        == "corpora/regressed-agent/hiveplane-corpus.yaml"
    )


def test_summary_records_pass_flag_and_renders(tmp_path: Path) -> None:
    runner = Runner(Api("http://localhost:8100"), tmp_path)

    runner.record("S26", "fanout-audit", "pass", "delivered", None)
    runner.record("S31", "pipeline-retry", "fail", "live child", None)

    assert runner.summary[0]["passed"] is True
    assert runner.summary[1]["passed"] is False

    runner.finish()

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert {record["scenario"] for record in summary["scenarios"]} == {"S26", "S31"}


def test_scenario_covers_the_supported_and_new_ids() -> None:
    """The runner registers every supported scenario plus the regressions/harness."""
    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    for sid in (
        "S1",
        "S11",
        "S25",
        "S26",
        "S27",
        "S28",
        "S29",
        "S30",
        "S31",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
    ):
        assert f'("{sid}",' in source, sid


def test_scenario_error_is_an_assertion_error() -> None:
    assert issubclass(ScenarioError, AssertionError)


#: The evidence contract every scenario directory must satisfy (M61-26, #595).
REQUIRED_EVIDENCE = (
    "run.log",
    "requests.log",
    "requests.json",
    "probe.json",
    "probe.md",
    "notes.md",
)

_SCENARIO_DIR = re.compile(r"^[SH]\d+-")


def test_committed_scenario_evidence_satisfies_the_contract() -> None:
    """Every committed scenario dir carries the full evidence set, so it can't regress."""
    results = ROOT / "field_test" / "v0.2.0" / "results"
    scenario_dirs = sorted(
        path for path in results.iterdir() if path.is_dir() and _SCENARIO_DIR.match(path.name)
    )
    assert scenario_dirs, "no scenario evidence committed"

    for directory in scenario_dirs:
        missing = [name for name in REQUIRED_EVIDENCE if not (directory / name).is_file()]
        assert not missing, f"{directory.name} is missing evidence: {missing}"


def test_api_records_response_shape_without_assuming_a_dict() -> None:
    """Non-dict/list bodies are classified, not assumed away (M61-17, #586)."""
    api = Api("http://localhost:9999")
    api._log_call(
        "GET", "/x", status=200, ms=1.0, tenant=None, request_body=None, response_body="oops"
    )
    api._log_call(
        "GET", "/x", status=200, ms=1.0, tenant=None, request_body={"a": 1}, response_body=[1, 2]
    )

    assert api.calls[0]["response_shape"] == "str"
    assert api.calls[0]["request_shape"] == "null"
    assert api.calls[1]["response_shape"] == "list"
    assert api.calls[1]["request_shape"] == "dict"
