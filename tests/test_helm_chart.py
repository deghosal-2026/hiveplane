"""Helm chart lint/render and k3d reference deploy (M59-01, M59-02)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_CHART = Path(__file__).resolve().parents[1] / "deploy" / "helm" / "hiveplane"
_HELM = shutil.which("helm")


def _render(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "hiveplane", str(_CHART), *extra],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_values_schema_is_valid_json() -> None:
    schema = json.loads((_CHART / "values.schema.json").read_text())
    assert schema["title"] == "HivePlane chart values"
    assert "api" in schema["properties"]


def test_chart_metadata_and_templates_exist() -> None:
    chart = yaml.safe_load((_CHART / "Chart.yaml").read_text())
    assert chart["name"] == "hiveplane"
    assert chart["version"] == "0.2.0"
    templates = {path.name for path in (_CHART / "templates").glob("*.yaml")}
    assert {
        "api.yaml",
        "ui.yaml",
        "postgres.yaml",
        "redis.yaml",
        "telemetry.yaml",
        "prometheus.yaml",
        "ingress.yaml",
        "secret.yaml",
        "serviceaccount.yaml",
    } <= templates


@pytest.mark.skipif(_HELM is None, reason="helm is not installed")
def test_helm_lint_and_render_full_stack() -> None:
    lint = subprocess.run(
        ["helm", "lint", str(_CHART)], capture_output=True, text=True, check=False
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr

    docs = _render()
    objects = {(str(doc["kind"]), str(doc["metadata"]["name"])) for doc in docs}
    names = {name for _, name in objects}
    kinds = {kind for kind, _ in objects}

    assert "Deployment" in kinds
    assert "StatefulSet" in kinds
    assert "ServiceAccount" in kinds
    assert {"Secret", "Service", "ConfigMap"} <= kinds
    assert {"api", "ui", "postgres", "redis"} <= {
        name.rsplit("-", 1)[-1] for name in names
    }
    postgres = next(doc for doc in docs if doc["kind"] == "StatefulSet")
    assert postgres["spec"]["template"]["spec"]["containers"][0]["name"] == "postgres"

    api = next(
        doc
        for doc in docs
        if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-api")
    )
    env = {item["name"] for item in api["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert {"HIVEPLANE_DATABASE__HOST", "HIVEPLANE_REDIS__HOST"} <= env


@pytest.mark.skipif(_HELM is None, reason="helm is not installed")
def test_ingress_renders_when_enabled() -> None:
    docs = _render("--set", "ingress.enabled=true")
    assert any(doc["kind"] == "Ingress" for doc in docs)
