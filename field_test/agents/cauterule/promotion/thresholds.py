"""Threshold configuration for the promotion gate.

Provides conservative / balanced / aggressive presets that control how the
promotion gate evaluates confidence, linter warnings, and conflict reports.
"""

from __future__ import annotations

from typing import Any

_THRESHOLD_PRESETS: dict[str, dict[str, float]] = {
    "conservative": {
        "precision": 1.0,
        "recall": 1.0,
        "min_confidence": 0.85,
        "min_history": 5,
        "linter_warning_limit": 0,
    },
    "balanced": {
        "precision": 1.0,
        "recall": 1.0,
        "min_confidence": 0.7,
        "min_history": 3,
        "linter_warning_limit": 0,
    },
    "aggressive": {
        "precision": 1.0,
        "recall": 0,
        "min_confidence": 0.5,
        "min_history": 1,
        "linter_warning_limit": 0,
    },
}

_VALID_MODES: frozenset[str] = frozenset({"conservative", "balanced", "aggressive"})


def get_thresholds(mode: str) -> dict[str, Any]:
    """Return threshold dict for the given *mode*.

    Args:
        mode: One of ``"conservative"``, ``"balanced"``, or ``"aggressive"``.

    Returns:
        A dict with keys ``precision``, ``recall``, ``min_confidence``,
        ``min_history``, ``linter_warning_limit``.

    Raises:
        ValueError: If *mode* is not recognised.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown threshold mode {mode!r}; choose one of {sorted(_VALID_MODES)}")
    return dict(_THRESHOLD_PRESETS[mode])


def set_thresholds(mode: str, thresholds: dict[str, Any]) -> None:
    """Update the preset for *mode* with *thresholds* (#596).

    Only the keys supplied in *thresholds* are overridden; other keys in
    the preset are preserved. Calibration adjustments persist in the
    process-wide preset so subsequent ``get_thresholds`` calls observe
    them.

    Args:
        mode: One of ``"conservative"``, ``"balanced"``, or ``"aggressive"``.
        thresholds: Mapping of threshold keys to new values.

    Raises:
        ValueError: If *mode* is not recognised.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown threshold mode {mode!r}; choose one of {sorted(_VALID_MODES)}")
    _THRESHOLD_PRESETS[mode].update(thresholds)
