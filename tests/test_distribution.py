"""Air-gap bundle, Homebrew formula, and release supply chain (M59-04/05/06)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def test_airgap_bundle_script_is_valid_shell() -> None:
    script = _ROOT / "scripts" / "airgap-bundle.sh"
    result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "docker save" in script.read_text()


def test_homebrew_formula_is_well_formed() -> None:
    formula = (_ROOT / "deploy" / "homebrew" / "hiveplane.rb").read_text()
    assert "class Hiveplane < Formula" in formula
    assert "desc " in formula
    assert "sha256 " in formula
    assert "def install" in formula
    assert "test do" in formula


def test_release_provenance_is_slsa_style(tmp_path: Path) -> None:
    out = tmp_path / "provenance.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts" / "release_provenance.py"),
            "--tag",
            "0.2.0",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=_ROOT,
    )
    assert result.returncode == 0, result.stderr
    provenance = json.loads(out.read_text())
    assert provenance["predicateType"] == "https://slsa.dev/provenance/v1"
    assert provenance["predicate"]["buildDefinition"]["externalParameters"]["tag"] == "0.2.0"
    assert any(subject["name"].endswith("Chart.yaml") for subject in provenance["subject"])


def test_release_workflow_has_the_supply_chain_jobs() -> None:
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "release.yml").read_text())
    assert set(workflow["jobs"]) >= {"build", "sbom", "sign", "provenance", "helm"}


def test_release_workflow_triggers_on_version_tags() -> None:
    text = (_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "tags:" in text
    assert "cosign" in text
    assert "sbom" in text
