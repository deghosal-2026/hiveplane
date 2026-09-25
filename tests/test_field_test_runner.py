from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import field_test_runner as runner_module  # noqa: E402
from field_test_runner import Runner, Api, ScenarioError  # noqa: E402
from hiveplane.core.manifest import load_manifest  # noqa: E402


def test_workload_evidence_points_at_real_field_test_assets(tmp_path: Path) -> None:
    runner = Runner(Api("http://localhost:8100"), tmp_path)

    evidence = runner.workload_evidence()

    assert evidence["workloads_root"] == "field_test/workloads"
    assert evidence["corpora_root"] == "field_test/corpora"
    for name, info in evidence["workloads"].items():
        assert info["manifest"].startswith("field_test/workloads/")
        assert info["benchmark_corpus_path"].startswith("field_test/corpora/")
        assert info["source"] == "field_test"


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

    assert support.spec.certification.benchmark_corpus == "corpora/support-agent/hiveplane-corpus.yaml"
    assert judge.spec.certification.benchmark_corpus == "corpora/eval-judge/hiveplane-corpus.yaml"
    assert regressed.spec.certification.benchmark_corpus == "corpora/regressed-agent/hiveplane-corpus.yaml"


def test_s6_writes_incremental_evidence_before_final_poll(tmp_path: Path) -> None:
    runner_module.ROOT = tmp_path
    runner = Runner(Api("http://localhost:8100"), tmp_path)
    runner.register = lambda name: None  # type: ignore[method-assign]
    runner._wait_for_state = lambda run_id, state, timeout=120.0: {  # type: ignore[method-assign]
        "id": run_id,
        "state": "paused",
    }

    class _Api:
        def request(self, method: str, path: str, payload=None, timeout: float = 600.0):
            if (method, path) == ("POST", "/runs"):
                return 201, {"id": "run-1", "state": "queued"}
            if (method, path) == ("POST", "/runs/run-1/start"):
                return 200, {"id": "run-1", "state": "running"}
            raise AssertionError((method, path, payload, timeout))

        def approve_and_resume(self, run_id: str):
            return {
                "pending": [{"approval_id": "ap-1", "run_id": run_id, "status": "pending"}],
                "approved": [{"approval_id": "ap-1", "status": 200, "body": {"status": "approved"}}],
                "resume": {"status": 200, "body": {"state": "running"}},
            }

        def poll(self, run_id: str, timeout: float = 300.0):
            raise ScenarioError(f"run {run_id} never terminal")

    runner.api = _Api()  # type: ignore[assignment]

    runner.s6_destructive()

    directory = tmp_path / "S6-destructive-tool"
    assert (directory / "submitted.json").is_file()
    assert (directory / "paused.json").is_file()
    assert (directory / "approvals.json").is_file()
