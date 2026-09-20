"""Optional local sentence-embedding similarity for paraphrase matching (#689).

Two triggers can be lexically dissimilar but semantically equivalent
("database connection dropped" vs "lost connection to postgres server").  The
lexical matcher scores those near zero.  This module provides a cosine
similarity between trigger and haystack using a small, CPU-friendly local
sentence-embedding model (``sentence-transformers/all-MiniLM-L6-v2``).

**Opt-in by design.** Loading an ~80MB model on first match would be a
surprising latency/network cost for a library, so semantic matching is disabled
unless explicitly enabled:

* set ``CAUTERULE_SEMANTIC_MATCHING=1``, or
* call :func:`set_embedder_for_testing` in tests.

When disabled (or when ``sentence-transformers``/the model is unavailable),
:func:`embedding_similarity` returns ``0.0`` and the matcher falls back to the
original lexical score, so behavior is unchanged for existing callers.
"""

from __future__ import annotations

import logging
import math
import os
import re
from functools import lru_cache
from typing import Protocol

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_ENV_FLAG = "CAUTERULE_SEMANTIC_MATCHING"
_WS_RE = re.compile(r"\s+")

# Cosine similarity at/above this is treated as strong paraphrase evidence.
# #721: lowered from 0.80. For a short trigger vs. a failure signature (not a
# long haystack), real MiniLM cosine for a true paraphrase lands ~0.60-0.70,
# so a 0.80 floor was unreachable and the semantic channel could not carry a
# match on its own. Below this floor the semantic term is only a minor blend
# contributor; at/above it the matcher floors the score to the curated
# threshold. Re-run scripts/calibrate_thresholds.py if this changes.
SEMANTIC_FLOOR = 0.62


class Embedder(Protocol):
    """Minimal embedding backend protocol."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...


class SentenceTransformerEmbedder:
    """Adapter around a ``sentence_transformers.SentenceTransformer``."""

    def __init__(self, model: object) -> None:
        self._model = model

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts)  # type: ignore[attr-defined]
        return [[float(x) for x in vector] for vector in vectors]


_embedder: Embedder | None = None
_resolved = False


def _env_enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_embedder() -> Embedder | None:
    global _embedder, _resolved
    if _resolved:
        return _embedder
    _resolved = True
    if not _env_enabled():
        _embedder = None
        return None
    try:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformerEmbedder(SentenceTransformer(_DEFAULT_MODEL))
    except Exception:
        # #710: enabling the flag without the optional dependency used to no-op
        # silently, so a sweep looked like "semantic matching has no effect".
        # Warn once so the operator knows it never ran.
        _log.warning(
            "%s is set but sentence-transformers is unavailable — semantic "
            "matching disabled. Install with: pip install cauterule[matching]",
            _ENV_FLAG,
        )
        _embedder = None
    return _embedder


def get_embedder() -> Embedder | None:
    """Return the active embedder, or None if semantic matching is disabled."""
    return _resolve_embedder()


def is_enabled() -> bool:
    """Return True if semantic matching is active."""
    return get_embedder() is not None


def set_embedder_for_testing(embedder: Embedder) -> None:
    """Install *embedder* and clear caches (tests / advanced callers)."""
    global _embedder, _resolved
    _embedder = embedder
    _resolved = True
    _embed_cached.cache_clear()


def reset_embedder() -> None:
    """Return to the disabled-by-default state and clear caches."""
    global _embedder, _resolved
    _embedder = None
    _resolved = False
    _embed_cached.cache_clear()


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text.lower()).strip()


@lru_cache(maxsize=8192)
def _embed_cached(text: str) -> tuple[float, ...] | None:
    embedder = get_embedder()
    if embedder is None:
        return None
    vector = embedder.encode([text])[0]
    return tuple(vector)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embedding_similarity(text_a: str, text_b: str) -> float:
    """Return cosine similarity in [0, 1]; 0.0 when semantic matching is off."""
    if get_embedder() is None:
        return 0.0
    vec_a = _embed_cached(_normalize(text_a))
    vec_b = _embed_cached(_normalize(text_b))
    if vec_a is None or vec_b is None:
        return 0.0
    return max(0.0, min(1.0, _cosine(vec_a, vec_b)))
