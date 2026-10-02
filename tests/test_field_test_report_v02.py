"""Tests for the v0.2.0 field-test report renderer (M61-21)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import field_test_report_v02 as report_module  # type: ignore[import-not-found]  # noqa: E402


def test_status_maps_pass_fail_blocked_and_missing() -> None:
    assert report_module._status(None) == "not run"
    assert report_module._status({"passed": True}) == "PASS"
    assert report_module._status({"passed": False}) == "FAIL"
    assert report_module._status({"status": "blocked", "passed": False}) == "BLOCKED"


def test_generated_report_names_asserting_scenarios_and_marks_gaps() -> None:
    summary = {
        "S12": {"passed": True, "detail": "paused"},
        "S13": {"status": "blocked", "passed": False, "detail": "needs --priced"},
    }
    rendered = report_module.render(summary, results_dir=ROOT / "field_test" / "v0.2.0" / "results")

    # Gate 20 (context budget) is evidenced by S12/S13.
    assert "| 20. Context budget exceeded -> clean pause with accounting | S12 |" in rendered
    # Gate 7 (k3d) has no asserting scenario and must be marked, not "load / review".
    assert "not yet demonstrated" in rendered
    assert "load / review" not in rendered
    # A blocked scenario is surfaced as such.
    assert "1 blocked" in rendered
