"""Demo sub-package (F-86, D11): hermetic roles + runner over the sample corpus.

The demo drives the real engine loop, re-gate, and replan end-to-end against
``examples/goals/`` with scripted (deterministic, zero-LLM) roles so it runs
anywhere. These constants namespace the demo's drift env var (D11 DD-M7-04):
the runner seeds it "open", flips it mid-run to force a stale re-gate, and
restores it afterwards.
"""

from __future__ import annotations

DEMO_WINDOW_VAR = "PC_DEMO_MAINTENANCE_WINDOW"
DEMO_WINDOW_EXPECTED = "open"
DEMO_WINDOW_DRIFT = "not-open"

__all__ = [
    "DEMO_WINDOW_DRIFT",
    "DEMO_WINDOW_EXPECTED",
    "DEMO_WINDOW_VAR",
]
