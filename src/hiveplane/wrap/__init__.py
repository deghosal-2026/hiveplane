"""``hiveplane wrap``: convert an existing app into an onboardable workload (M31)."""

from __future__ import annotations

from hiveplane.wrap.detect import Detection, WrapError, detect
from hiveplane.wrap.job import WrapPlan, plan_wrap, write_wrap
from hiveplane.wrap.scaffold import render_files

__all__ = [
    "Detection",
    "WrapError",
    "WrapPlan",
    "detect",
    "plan_wrap",
    "render_files",
    "write_wrap",
]
