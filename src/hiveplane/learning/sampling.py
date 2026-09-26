"""Deterministic online-eval sampling and PII-safe guardrails (M36-05, M36-07).

Selection is a stable function of the run id (``sha256(run_id) mod 100``), so a
given run is always sampled or never sampled regardless of timing or retries.
"""

from __future__ import annotations

import hashlib
import json

from hiveplane.core.run import Run


def sample_bucket(run_id: str) -> int:
    """Return a stable bucket in ``[0, 100)`` for a run id."""
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def should_sample(run_id: str, sample_rate: int) -> bool:
    """Return True when a run falls in the configured sample percentage."""
    if sample_rate >= 100:
        return True
    if sample_rate <= 0:
        return False
    return sample_bucket(run_id) < sample_rate


def is_pii(run: Run, patterns: tuple[str, ...] = ()) -> bool:
    """Return True when a run's task matches any sensitive pattern.

    Matching is a case-insensitive substring test over the serialized task so a
    run marked with sensitive keys/values is never sent to the judge.
    """
    if not patterns:
        return False
    haystack = json.dumps(run.task, sort_keys=True, default=str).lower()
    return any(pattern.lower() in haystack for pattern in patterns)
