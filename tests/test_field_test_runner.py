from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from field_test_runner import Runner, Api  # noqa: E402
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
