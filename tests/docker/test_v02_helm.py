"""L10 (v0.2.0) — Helm / k3d reference deployment (M59, M61-08, gate 7).

Renders and lints the chart locally; the k3d deploy is exercised by
``deploy/k3d/up.sh`` (documented in the docker test plan). Requires ``helm`` on the
runner — a missing ``helm`` is a documented environment failure for the release run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

_ROOT = Path(__file__).resolve().parents[2]
_CHART = _ROOT / "deploy" / "helm" / "hiveplane"


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail("helm is required for the L10 release run (install helm)", pytrace=False)
    return subprocess.run(
        [helm, *args], capture_output=True, text=True, timeout=300
    )


def test_chart_lints() -> None:
    result = _helm("lint", str(_CHART))
    assert result.returncode == 0, result.stderr


def test_chart_renders_the_full_stack() -> None:
    result = _helm(
        "template", "hiveplane", str(_CHART), "--set", "api.enabled=true"
    )
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    for expected in ("Deployment", "Service", "StatefulSet"):
        assert expected in rendered, f"chart must render a {expected}"


def test_chart_values_schema_is_present() -> None:
    assert (_CHART / "values.schema.json").is_file()
