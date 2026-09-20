"""Determinism guarantee."""

from __future__ import annotations

import hashlib
import json

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import DEFAULT_THRESHOLD
from cauterule.replay.report import build_evidence_report


def _corpus_hash(trajectories: list[Trajectory]) -> str:
    """Deterministic content hash of a corpus.

    Digests each trajectory's content (id, task, failure_class, success,
    failure_point, and step tool/input/error/output) so an id-preserving
    content change — including flipping the success flag — changes the
    hash (code-review, #520).
    """
    parts: list[str] = []
    for t in sorted(trajectories, key=lambda t: t.id):
        steps = [f"{s.tool}|{s.input}|{s.error}|{s.output}" for s in t.steps]
        meta = f"{t.id}|{t.task}|{t.failure_class}|{t.success}|{t.failure_point}|{t.domain}"
        parts.append(f"{meta}|{'|'.join(steps)}")
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:8]


def deterministic_replay(
    candidate: CandidateRule,
    trajectories: list[Trajectory],
    threshold: float = DEFAULT_THRESHOLD,
) -> EvidenceReport:
    """Replay with determinism guarantee.

    Same candidate + same corpus (by content) + same threshold = same report
    (no randomness). Achieved by sorting trajectories by id and using a
    deterministic scorer.  The returned report carries ``corpus_hash`` so a
    caller caching a verdict can invalidate it when the corpus changes (#520).
    """
    sorted_trajs = sorted(trajectories, key=lambda t: t.id)
    corpus_hash = _corpus_hash(sorted_trajs)
    report = build_evidence_report(candidate, sorted_trajs, threshold)
    from dataclasses import replace

    return replace(report, corpus_hash=corpus_hash)
