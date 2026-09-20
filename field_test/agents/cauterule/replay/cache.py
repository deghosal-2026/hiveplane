"""Replay caching — cache replay results for same candidate + corpus hash."""

from __future__ import annotations

import hashlib
import json
import threading

from cauterule.models.candidate import CandidateRule
from cauterule.models.evidence import EvidenceReport
from cauterule.models.trajectory import Trajectory
from cauterule.replay.determinism import deterministic_replay
from cauterule.replay.matcher import DEFAULT_THRESHOLD


class ReplayCache:
    """Cache replay results keyed by candidate + corpus hash."""

    def __init__(self) -> None:
        self._cache: dict[str, EvidenceReport] = {}
        self._lock = threading.Lock()

    def _key(
        self,
        candidate: CandidateRule,
        trajectories: list[Trajectory],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> str:
        # Content-aware key (#506): id-only hashing served stale verdicts
        # after content edits. Hash canonical trajectory content, candidate
        # confidence, and the active threshold. Full hex — no truncation.
        corpus = sorted(
            (t.to_dict() for t in trajectories),
            key=lambda d: str(d.get("trajectory_id", "")),
        )
        corpus_hash = hashlib.sha256(
            json.dumps(corpus, sort_keys=True, default=str).encode()
        ).hexdigest()
        cand_hash = hashlib.sha256(
            json.dumps(
                {
                    # Hash the whole ``when`` dict so future RuleWhen fields
                    # (e.g. ``signature``, #763) can never be silently omitted.
                    "when": candidate.when.to_dict(),
                    "directive": candidate.do.directive,
                    "confidence": candidate.confidence,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return f"{cand_hash}:{corpus_hash}:{threshold}"

    def get(
        self,
        candidate: CandidateRule,
        trajectories: list[Trajectory],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> EvidenceReport:
        """Return cached or computed replay result."""
        key = self._key(candidate, trajectories, threshold)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        report = deterministic_replay(candidate, trajectories, threshold)
        with self._lock:
            self._cache[key] = report
        return report

    def clear(self) -> None:
        """Clear the cache."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
