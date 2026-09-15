"""Tests for the workload manifest spec, model, parser, and schema export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from hiveplane.core.manifest import (
    ManifestError,
    load_manifest,
    manifest_json_schema,
    parse_manifest,
)
from hiveplane.core.workload import AgentWorkload


def _manifest(**spec_overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
        "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
        "model": {
            "strategy": "tiered",
            "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
        },
        "certification": {
            "benchmark_corpus": "corpora/repo-agent/v1",
            "staging_threshold": 0.8,
            "production_threshold": 0.9,
            "status": "uncertified",
        },
    }
    spec.update(spec_overrides)
    return {
        "apiVersion": "hiveplane/v1",
        "kind": "AgentWorkload",
        "metadata": {"name": "repo-agent", "owner": "platform-team", "team": "platform"},
        "spec": spec,
    }


def test_valid_manifest_parses() -> None:
    workload = parse_manifest(_manifest())

    assert workload.metadata.name == "repo-agent"
    assert workload.owner == "platform-team"
    assert workload.team == "platform"
    assert workload.certification_status.value == "uncertified"
    assert workload.spec.runtime.adapter.value == "raw-worker"


def test_unknown_top_level_field_is_rejected() -> None:
    data = _manifest()
    data["bogus"] = True

    with pytest.raises(ValidationError, match="bogus"):
        parse_manifest(data)


def test_unknown_spec_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="nonsense"):
        parse_manifest(_manifest(nonsense=True))


def test_invalid_name_is_rejected() -> None:
    data = _manifest()
    data["metadata"]["name"] = "Bad_Name"

    with pytest.raises(ValidationError, match=r"(?i)dns-safe"):
        parse_manifest(data)


def test_budget_ordering_is_enforced() -> None:
    with pytest.raises(ValidationError, match="per_day_usd"):
        parse_manifest(_manifest(budget={"per_run_usd": 5.0, "per_day_usd": 1.0}))


def test_team_budget_ordering_is_enforced() -> None:
    with pytest.raises(ValidationError, match="per_team_usd"):
        parse_manifest(
            _manifest(
                budget={"per_run_usd": 1.0, "per_day_usd": 2.0, "per_team_usd": 1.0}
            )
        )


def test_workload_without_certification_is_uncertified() -> None:
    data = _manifest()
    del data["spec"]["certification"]

    workload = parse_manifest(data)

    assert workload.certification_status.value == "uncertified"


def test_load_manifest_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n")

    with pytest.raises(ManifestError, match="mapping"):
        load_manifest(path)


def test_certification_threshold_ordering_is_enforced() -> None:
    certification = {
        "benchmark_corpus": "corpora/x/v1",
        "staging_threshold": 0.95,
        "production_threshold": 0.9,
        "status": "uncertified",
    }

    with pytest.raises(ValidationError, match="production_threshold"):
        parse_manifest(_manifest(certification=certification))


def test_certified_status_requires_attestation_id() -> None:
    certification = {
        "benchmark_corpus": "corpora/x/v1",
        "staging_threshold": 0.8,
        "production_threshold": 0.9,
        "status": "certified",
    }

    with pytest.raises(ValidationError, match="attestation_id"):
        parse_manifest(_manifest(certification=certification))


def test_certified_status_with_expired_attestation_is_rejected() -> None:
    certification = {
        "benchmark_corpus": "corpora/x/v1",
        "staging_threshold": 0.8,
        "production_threshold": 0.9,
        "status": "certified",
        "attestation_id": "att-1",
        "expires_at": "2020-01-01T00:00:00Z",
    }

    with pytest.raises(ValidationError, match="expires_at"):
        parse_manifest(_manifest(certification=certification))


def test_certification_requires_model_identity() -> None:
    spec = _manifest()
    del spec["spec"]["model"]["identity"]

    with pytest.raises(ValidationError, match="identity"):
        parse_manifest(spec)


def test_manifest_rejects_unknown_adapter() -> None:
    with pytest.raises(ValidationError):
        parse_manifest(
            _manifest(runtime={"adapter": "autogen", "entrypoint": "x:y"})
        )


def test_load_manifest_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "repo-agent.yaml"
    path.write_text(yaml.safe_dump(_manifest()))

    workload = load_manifest(path)

    assert isinstance(workload, AgentWorkload)
    assert workload.metadata.name == "repo-agent"


def test_load_manifest_reports_yaml_parse_errors(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("metadata: [unclosed\n")

    with pytest.raises(ManifestError, match="parse"):
        load_manifest(path)


def test_load_manifest_reports_validation_errors(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump({"apiVersion": "hiveplane/v1", "kind": "AgentWorkload"}))

    with pytest.raises(ManifestError, match="invalid"):
        load_manifest(path)


def test_json_schema_export() -> None:
    schema = manifest_json_schema()

    assert schema["title"] == "AgentWorkload"
    assert "properties" in schema


def test_published_schema_is_current() -> None:
    import json

    published_path = (
        Path(__file__).resolve().parents[1] / "docs" / "workloads" / "manifest.schema.json"
    )
    published = json.loads(published_path.read_text(encoding="utf-8"))
    expected = manifest_json_schema()
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"

    assert published == expected


def test_manifest_is_strict_about_extra_tool_fields() -> None:
    tools = {"allow": [{"tool_id": "github.read_issue", "trust_level": "read_only", "x": 1}]}

    with pytest.raises(ValidationError):
        parse_manifest(_manifest(tools=tools))
