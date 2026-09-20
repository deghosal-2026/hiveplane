"""Failure taxonomy enforcement on promote (#586).

Controlled vocabulary, keyword auto-classification (with confidence), the
promote gate, and store backfill. Clustering-backed labels map through
:func:`normalize_taxonomy` so future cluster outputs stay valid.
"""

from __future__ import annotations

from typing import Any

VOCABULARY = frozenset(
    {
        "git/push",
        "git/pull",
        "git/merge",
        "python/import",
        "python/syntax",
        "python/pip",
        "tool/network",
        "tool/permission",
        "config/env",
        "test/flaky",
        "docker/build",
        "docker/network",
        "docker/container",
        "other",
    }
)

CONFIDENCE_THRESHOLD = 0.5

_KEYWORDS: dict[str, tuple[str, float]] = {
    "non-fast-forward": ("git/push", 0.9),
    "git push": ("git/push", 0.85),
    "rejected": ("git/push", 0.7),
    "git pull": ("git/pull", 0.85),
    "git merge": ("git/merge", 0.85),
    "modulenotfounderror": ("python/import", 0.9),
    "module not found": ("python/import", 0.9),
    "no module named": ("python/import", 0.9),
    "syntaxerror": ("python/syntax", 0.9),
    "invalid syntax": ("python/syntax", 0.85),
    "indentationerror": ("python/syntax", 0.85),
    "connection refused": ("tool/network", 0.85),
    "network unreachable": ("tool/network", 0.85),
    "econnrefused": ("tool/network", 0.85),
    "timed out": ("tool/network", 0.75),
    "permission denied": ("tool/permission", 0.9),
    "eacces": ("tool/permission", 0.9),
    "unauthorized": ("tool/permission", 0.75),
    "environment variable": ("config/env", 0.85),
    "env var": ("config/env", 0.85),
    "not set": ("config/env", 0.7),
    ".env": ("config/env", 0.7),
    "flaky": ("test/flaky", 0.9),
    "intermittent": ("test/flaky", 0.8),
    "order-dependent": ("test/flaky", 0.8),
    "passes alone": ("test/flaky", 0.8),
    "dockerfile": ("docker/build", 0.85),
    "docker build": ("docker/build", 0.85),
    "compose": ("docker/network", 0.75),
    "container": ("docker/container", 0.75),
    "pip install": ("python/pip", 0.85),
    "resolutionimpossible": ("python/pip", 0.9),
}


def normalize_taxonomy(label: str) -> str:
    """Map a clustering/extended label into the vocabulary (or 'other').

    Exact matches pass through; longer labels collapse to the longest
    vocabulary prefix (``git/push/rejected`` → ``git/push``).
    """
    text = (label or "").strip().lower()
    if text in VOCABULARY:
        return text
    for candidate in sorted(VOCABULARY, key=len, reverse=True):
        if text == candidate or text.startswith(candidate + "/"):
            return candidate
    return "other"


def validate_taxonomy(value: str) -> str:
    """Validate a taxonomy value. Returns the normalized label."""
    normalized = normalize_taxonomy(value)
    if (value or "").strip().lower() not in VOCABULARY and normalized == "other":
        msg = (
            f"unknown taxonomy {value!r}: want one of {sorted(VOCABULARY)} "
            "(or pass --taxonomy other explicitly)"
        )
        raise ValueError(msg)
    return normalized


def classify_with_confidence(trigger: str, directive: str = "") -> tuple[str, float]:
    """Keyword auto-classification. Returns (label, confidence)."""
    haystack = f"{trigger} {directive}".lower()
    best: tuple[str, float] | None = None
    for keyword in sorted(_KEYWORDS, key=len, reverse=True):
        if keyword in haystack:
            label, confidence = _KEYWORDS[keyword]
            if best is None or confidence > best[1]:
                best = (label, confidence)
    if best:
        return best
    return ("other", 0.3)


def ensure_taxonomy(
    trigger: str,
    directive: str = "",
    explicit: str | None = None,
) -> tuple[str, bool]:
    """Resolve the taxonomy for a promotion. Returns (label, auto_classified).

    Raises ValueError for unknown explicit values, or when auto-classification
    confidence is below threshold without an explicit value.
    """
    if explicit:
        return validate_taxonomy(explicit), False
    label, confidence = classify_with_confidence(trigger, directive)
    if confidence < CONFIDENCE_THRESHOLD:
        msg = (
            f"cannot auto-classify taxonomy for trigger {trigger!r} "
            f"(confidence {confidence:.2f} < {CONFIDENCE_THRESHOLD}); "
            "pass --taxonomy <value> explicitly (other allowed)"
        )
        raise ValueError(msg)
    return label, True


def backfill(store_dir: str = "rules", dry_run: bool = False) -> dict[str, Any]:
    """Classify store rules missing taxonomy. Returns a report dict."""
    from cauterule.store.manager import StoreManager

    manager = StoreManager(base_dir=store_dir)
    updated: list[str] = []
    skipped: list[str] = []
    for rule in manager.list_rules():
        if rule.taxonomy:
            continue
        label, confidence = classify_with_confidence(rule.when.trigger, rule.do.directive)
        if confidence < CONFIDENCE_THRESHOLD:
            skipped.append(rule.id)
            continue
        if not dry_run:
            import dataclasses

            manager.add_rule(dataclasses.replace(rule, taxonomy=label))
        updated.append(f"{rule.id}->{label}")
    return {"updated": updated, "skipped": skipped, "dry_run": dry_run}
