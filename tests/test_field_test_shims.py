from __future__ import annotations

from pathlib import Path

from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.core.manifest import load_manifest

_ROOT = Path(__file__).resolve().parents[1]
_WORKLOADS_DIR = _ROOT / "field_test" / "workloads"
_CORPORA_DIR = _ROOT / "field_test" / "corpora"


def test_real_field_test_shim_entrypoints_are_loadable() -> None:
    loader = EntrypointLoader(root=_ROOT)

    support = loader.load("field_test.shims.support_agent:run")
    regressed = loader.load("field_test.shims.regressed_agent:run")
    judge = loader.load_object("field_test.shims.eval_judge:graph")

    assert callable(support)
    assert callable(regressed)
    assert callable(getattr(judge, "stream", None))
    assert callable(getattr(judge, "get_state", None))


def test_real_field_test_workloads_reference_shims_and_corpora() -> None:
    support = load_manifest(_WORKLOADS_DIR / "support-agent.yaml")
    judge = load_manifest(_WORKLOADS_DIR / "eval-judge.yaml")

    assert support.spec.runtime.entrypoint == "field_test.shims.support_agent:run"
    assert judge.spec.runtime.entrypoint == "field_test.shims.eval_judge:graph"
    assert support.spec.certification is not None
    assert judge.spec.certification is not None
    assert (
        support.spec.certification.benchmark_corpus
        == "corpora/support-agent/hiveplane-corpus.yaml"
    )
    assert judge.spec.certification.benchmark_corpus == "corpora/eval-judge/hiveplane-corpus.yaml"
    assert (_CORPORA_DIR / "support-agent" / "hiveplane-corpus.yaml").is_file()
    assert (_CORPORA_DIR / "eval-judge" / "hiveplane-corpus.yaml").is_file()
    assert (_CORPORA_DIR / "regressed-agent" / "hiveplane-corpus.yaml").is_file()
