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


def test_compose_defines_healthchecked_operator_ui() -> None:
    services = _load("docker-compose.yml")["services"]
    ui = services["ui"]

    assert ui["environment"]["HIVEPLANE_UI__API_URL"] == "http://api:8000"
    assert any(str(port).endswith(":8000") for port in ui["ports"])
    assert ui.get("healthcheck") is not None
    assert "api" in ui["depends_on"]


def test_dockerfile_copies_examples_and_installs_langgraph_extra() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY examples" in dockerfile, "image must copy examples/ for entrypoints"
    assert "langgraph" in dockerfile, "image must install the langgraph extra"
    assert "HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT" in dockerfile
    assert "/app" in dockerfile


def test_compose_api_selects_postgres_store_and_raw_worker_adapter() -> None:
    services = _load("docker-compose.yml")["services"]
    environment = services["api"]["environment"]

    assert environment["HIVEPLANE_EXECUTION__ADAPTER"] == "raw-worker"
    assert environment["HIVEPLANE_EXECUTION__STORE"] == "postgres"


def test_compose_api_moves_off_the_omlx_port() -> None:
    services = _load("docker-compose.yml")["services"]
    api = services["api"]

    host_mapping = str(api["ports"][0])
    assert host_mapping.startswith("${API_PORT:-8100}") or "8100" in host_mapping, (
        f"api host port must default to 8100, got {host_mapping!r}"
    )
    assert host_mapping.endswith(":8000")


def test_compose_defines_llm_provider_profiles() -> None:
    services = _load("docker-compose.yml")["services"]

    assert "test" in services["webhook-sink"]["profiles"]


def test_no_compose_service_binds_host_port_8000() -> None:
    """Host port 8000 is reserved for the local OMLX server (field test, M23)."""
    services = _load("docker-compose.yml")["services"]

    for name, service in services.items():
        for port in service.get("ports", []):
            host_port = str(port).split(":")[0]
            assert host_port != "8000", f"{name} binds host port 8000 (reserved for OMLX)"


def test_env_profiles_set_llm_provider_defaults() -> None:
    ci = (_ROOT / ".env.ci").read_text(encoding="utf-8")
    local = (_ROOT / ".env.local").read_text(encoding="utf-8")
    cloud = (_ROOT / ".env.cloud").read_text(encoding="utf-8")

    assert "HIVEPLANE_MODEL__PROVIDER=fake" in ci

    assert "HIVEPLANE_MODEL__PROVIDER=local" in local
    assert "HIVEPLANE_MODEL__BASE_URL=http://host.docker.internal:8000/v1" in local
    assert (
        '{"mlx-community/Qwen2.5-7B-Instruct-4bit": "omlx/qwen2.5-7b-instruct/4bit"}'
        in local
    ), "local profile must alias the OMLX-served model to the canonical identity"
    assert "HIVEPLANE_MODEL__DEFAULT_MODEL=omlx/qwen2.5-7b-instruct/4bit" in local

    assert "HIVEPLANE_MODEL__PROVIDER=cloud" in cloud
    assert "HIVEPLANE_MODEL__BASE_URL=https://api.openai.com/v1" in cloud
    assert "HIVEPLANE_MODEL__API_KEY=" in cloud
    assert (
        '{"gpt-4o-2024-08-06": "openai/gpt-4o/2024-08-06"}' in cloud
    ), "cloud profile must alias the served model name to the canonical identity"


def test_compose_maps_model_alias_env_to_the_api_container() -> None:
    services = _load("docker-compose.yml")["services"]
    environment = services["api"]["environment"]

    assert "HIVEPLANE_MODEL__MODEL_ALIASES" in environment


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
