"""Negative-scenario field-test fixtures (M23, #93).

The docker/field-test governance scenarios (S2 uncertified, S3 model-swap, S4
regressed promotion) are driven by these bundled workloads and the deliberately
broken agent they point at.
"""

from __future__ import annotations

from pathlib import Path

from hiveplane.core.manifest import load_manifest

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"
CORPORA_DIR = Path(__file__).resolve().parents[1] / "examples" / "corpora"

_NEGATIVE_FIXTURES = {
    "uncertified-agent": "examples.repo_agent:run",
    "model-swap-agent": "examples.repo_agent:run",
    "regressed-agent": "examples.regressed_agent:run",
}


def test_negative_fixtures_load_and_are_uncertified() -> None:
    for name, entrypoint in _NEGATIVE_FIXTURES.items():
        workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")

        assert workload.name == name
        assert workload.spec.runtime.entrypoint == entrypoint
        assert workload.certification_status.value == "uncertified"


def test_negative_fixtures_link_to_an_existing_corpus() -> None:
    for name in _NEGATIVE_FIXTURES:
        workload = load_manifest(EXAMPLES_DIR / f"{name}.yaml")
        certification = workload.spec.certification
        assert certification is not None and certification.benchmark_corpus is not None
        relative = certification.benchmark_corpus.removeprefix("corpora/")
        assert (CORPORA_DIR / relative / "corpus.yaml").is_file(), name


def test_regressed_agent_returns_the_seeded_wrong_answer() -> None:
    from examples.regressed_agent import run

    assert run({}, None) == {"risk": "low", "summary": "looks fine"}  # type: ignore[arg-type]
