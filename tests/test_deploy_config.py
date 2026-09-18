"""Tests that the local observability stack starts with no manual steps (M19, #49)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def _load(relative: str) -> dict[str, Any]:
    loaded = yaml.safe_load((_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_compose_runs_healthchecked_observability_stack() -> None:
    services = _load("docker-compose.yml")["services"]

    for name in ("otel-collector", "tempo", "prometheus", "grafana"):
        assert name in services, f"{name} service missing"
        assert services[name].get("healthcheck") is not None, f"{name} has no healthcheck"


def test_collector_exports_traces_to_tempo_and_metrics_to_prometheus() -> None:
    collector = _load("deploy/otel/otel-collector.yaml")
    pipelines = collector["service"]["pipelines"]

    assert "otlp/tempo" in pipelines["traces"]["exporters"]
    assert "prometheus" in pipelines["metrics"]["exporters"]
    assert "health_check" in collector["service"]["extensions"]
    telemetry = collector["service"]["telemetry"]
    assert telemetry["metrics"]["address"].endswith(":8888")


def test_prometheus_scrapes_the_collector_and_tempo() -> None:
    prometheus = _load("deploy/prometheus/prometheus.yml")
    jobs = {job["job_name"] for job in prometheus["scrape_configs"]}

    assert {"otel-collector", "otel-collector-internal", "tempo"} <= jobs


def test_grafana_provisions_dashboards_from_compose() -> None:
    services = _load("docker-compose.yml")["services"]
    volumes = services["grafana"]["volumes"]

    assert any("dashboards" in volume for volume in volumes)

    provider = _load("deploy/grafana/provisioning/dashboards/dashboards.yaml")
    options = provider["providers"][0]["options"]
    assert options["path"].startswith("/")
    assert options["foldersFromFilesStructure"] in (True, False)


def test_overview_dashboard_is_provisionable() -> None:
    dashboard = json.loads(
        (_ROOT / "deploy/grafana/dashboards/hiveplane-overview.json").read_text(
            encoding="utf-8"
        )
    )

    assert dashboard["title"]
    assert len(dashboard["panels"]) >= 2


def test_overview_dashboard_covers_fleet_and_certification_metrics() -> None:
    dashboard = json.loads(
        (_ROOT / "deploy/grafana/dashboards/hiveplane-overview.json").read_text(
            encoding="utf-8"
        )
    )
    expressions = " ".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    )

    for metric in (
        "hiveplane_runs_total",
        "hiveplane_failures_total",
        "hiveplane_escalations_total",
        "hiveplane_budget_burn_usd",
        "hiveplane_spend_usd_total",
        "hiveplane_intervention_latency_seconds_bucket",
        "hiveplane_certifications_total",
        "hiveplane_attestation_verifications_total",
        "hiveplane_model_swap_blocks_total",
    ):
        assert metric in expressions, f"{metric} missing from dashboard"
