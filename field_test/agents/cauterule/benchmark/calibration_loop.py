"""Calibration feedback loop — adjusts promotion thresholds from benchmark data.

Stores a persistent calibration history so repeated low-precision or
low-confidence results tighten thresholds over time (#596).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from cauterule.promotion.thresholds import get_thresholds, set_thresholds

_CALIBRATION_FILE = "calibration_history.json"


def _history_path() -> Path:
    return Path(_CALIBRATION_FILE)


def _load_history() -> list[dict[str, Any]]:
    """Load calibration entries, accepting legacy flat-dict files (#801).

    The current format is a JSON list of timestamped threshold snapshots.
    Older files stored a single flat ``{key: value}`` dict; that is read as a
    one-entry history so existing installations keep their accumulated signal.
    """
    try:
        raw: Any = json.loads(_history_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        entries = raw.get("entries")
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]
        # Legacy format: a single flat threshold dict.
        return [raw] if raw else []
    return []


def _save_history(updates: dict[str, Any]) -> None:
    history = _load_history()
    entry = dict(updates)
    entry["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    history.append(entry)
    _history_path().write_text(json.dumps(history, indent=2), encoding="utf-8")


def feed_calibration_data(thresholds: dict[str, Any]) -> dict[str, Any]:
    """Feed calibration data back into the promotion threshold system.

    Args:
        thresholds: A dict with keys like ``"min_confidence"``, ``"min_precision"``,
            ``"min_recall"``, ``"linter_warning_limit"``, ``"conflict_tolerance"``.

    Returns:
        A dict mapping preset names to thresholds that were adjusted.
        Adjustments are persisted in ``calibration_history.json`` and
        compounded across successive feed calls.
    """
    _validate_thresholds(thresholds)

    # Persist calibration signal.
    _save_history(thresholds)

    # Compute adjusted thresholds: increase tightness when precision is low.
    precision = thresholds.get("min_precision", 0.0)
    history = _load_history()

    adjustment = 0.0
    # Multiple low-precision reports tighten thresholds.
    low_precision_count = sum(
        1
        for entry in history
        if isinstance(entry.get("min_precision"), (int, float))
        and entry["min_precision"] < 0.8
    )
    if low_precision_count >= 3:
        adjustment = 0.05  # tighten by 5%
    elif precision < 0.8 and low_precision_count >= 1:
        adjustment = 0.02

    adjusted: dict[str, Any] = {}
    for mode in ("conservative", "balanced", "aggressive"):
        current = get_thresholds(mode)
        adjusted_thresholds = dict(current)
        if "min_confidence" in current and adjustment > 0:
            adjusted_thresholds["min_confidence"] = min(1.0, current["min_confidence"] + adjustment)
        if "min_precision" in current and adjustment > 0:
            adjusted_thresholds["min_precision"] = min(1.0, current["min_precision"] + adjustment)
        # Apply the adjusted thresholds.
        set_thresholds(mode, adjusted_thresholds)
        adjusted[mode] = adjusted_thresholds

    return adjusted


def _validate_thresholds(thresholds: dict[str, Any]) -> None:
    required_keys = {
        "min_confidence",
        "min_precision",
        "min_recall",
        "linter_warning_limit",
        "conflict_tolerance",
    }
    missing = required_keys - set(thresholds.keys())
    if missing:
        raise ValueError(f"Missing required threshold keys: {missing}")
    for key in ("min_confidence", "min_precision", "min_recall"):
        val = thresholds.get(key, 0.0)
        if not isinstance(val, (int, float)) or not 0.0 <= val <= 1.0:
            raise ValueError(f"{key} must be in [0.0, 1.0], got {val}")
    for key in ("linter_warning_limit", "conflict_tolerance"):
        val = thresholds.get(key, 0)
        if not isinstance(val, int) or val < 0:
            raise ValueError(f"{key} must be non-negative int, got {val}")
