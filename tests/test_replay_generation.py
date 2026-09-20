"""Tests for the shipped LLM replay fixture (M23, #137).

The checked-in replay file must cover every corpus task of every example
workload, satisfy its exact-match check, and be byte-identical to what the
generator produces — prompt or agent changes force a conscious regeneration.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from hiveplane.certification.models import CheckType

ROOT = Path(__file__).resolve().parents[1]
REPLAY_FILE = ROOT / "deploy" / "testdata" / "llm" / "replay.json"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_replay", ROOT / "scripts" / "generate_replay.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - path guard
        raise RuntimeError("generator module not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_replay"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_replay_satisfies_every_corpus_task() -> None:
    generator = _load_generator()
    replay = json.loads(REPLAY_FILE.read_text(encoding="utf-8"))

    covered = 0
    for workload in generator.WORKLOADS:
        manifest = generator.load_manifest(generator.WORKLOADS_DIR / f"{workload}.yaml")
        corpus_dir = generator.CORPORA_DIR / workload / generator.CORPUS_DIRS[workload]
        corpus = generator.load_corpus(corpus_dir)
        model = generator.canonical_model_identity(manifest.spec.model.identity)
        for task in corpus.tasks:
            ctx = generator.CaptureContext(
                model, generator.ShapingPipeline(generator.InjectionScanner()),
                manifest.spec.output_shaping,
            )
            key = generator.drive_task(workload, manifest.spec.runtime.entrypoint, task, ctx)
            content = generator.expected_completion(workload, task)

            assert key in replay, f"{workload}/{task.id}: replay entry missing"
            assert replay[key] == content, f"{workload}/{task.id}: replay content drifted"

            parsed = json.loads(content)
            if task.check.type is CheckType.EXACT_MATCH:
                assert parsed[task.check.field] == task.check.value, (
                    f"{workload}/{task.id}: replay does not satisfy the check"
                )
            covered += 1

    assert covered == len(replay), "replay file has orphaned entries"


def test_generation_is_deterministic_and_matches_the_committed_file() -> None:
    generator = _load_generator()

    first = generator.generate_replay()
    second = generator.generate_replay()

    assert first == second
    committed = json.loads(REPLAY_FILE.read_text(encoding="utf-8"))
    assert first == committed
