"""Tests for certification records, regression diff, and the coordinator (M7, #25)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hiveplane.certification.diff import regression_diff
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkResult,
    BenchmarkTaskResult,
    CertificationPolicy,
    CertificationStatus,
    CheckStatus,
    Environment,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.runner import ReferenceExecutor
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)

_TASKS: list[dict[str, Any]] = [
    {
        "id": "t1",
        "name": "classify low risk",
        "check": {"type": "exact_match", "field": "risk", "value": "low"},
    },
    {
        "id": "t2",
        "name": "flag risky change",
        "check": {
            "type": "action_audit",
            "required_actions": ["request_changes"],
            "forbidden_actions": ["merge_pull_request"],
        },
    },
]


def _policy() -> CertificationPolicy:
    return CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.70, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85,
            max_critical_failures=0,
            max_p95_latency_ms=30000,
            min_production_runs_survived=0,
        ),
    )


def _task_result(task_id: str, status: CheckStatus) -> BenchmarkTaskResult:
    return BenchmarkTaskResult(task_id=task_id, status=status, latency_ms=1)


def _result(
    tasks: list[BenchmarkTaskResult], *, run_id: str = "br-1", model: str = "m"
) -> BenchmarkResult:
    passed = sum(1 for task in tasks if task.status is CheckStatus.PASS)
    return BenchmarkResult(
        benchmark_run_id=run_id,
        workload_id="repo-agent",
        manifest_version=1,
        corpus_id="corpus",
        corpus_version=1,
        model_identity=model,
        environment=_ENV,
        started_at=_FIXED_NOW,
        finished_at=_FIXED_NOW,
        tasks=tasks,
        aggregate=BenchmarkAggregate(
            total=len(tasks),
            passed=passed,
            failed=len(tasks) - passed,
            pass_rate=passed / len(tasks) if tasks else 0.0,
            critical_failures=0,
            p50_latency_ms=1,
            p95_latency_ms=1,
            total_tokens=0,
        ),
    )


# --------------------------------------------------------------------------- #
# Regression diff
# --------------------------------------------------------------------------- #
def test_regression_diff_flags_regressed_and_improved() -> None:
    before = _result(
        [
            _task_result("t1", CheckStatus.PASS),
            _task_result("t2", CheckStatus.PASS),
            _task_result("t3", CheckStatus.FAIL),
        ]
    )
    after = _result(
        [
            _task_result("t1", CheckStatus.PASS),
            _task_result("t2", CheckStatus.FAIL),
            _task_result("t3", CheckStatus.PASS),
        ]
    )

    diff = regression_diff(
        before, after, before_attestation_id="att-1", after_attestation_id="att-2"
    )

    assert [item.task_id for item in diff.regressed] == ["t2"]
    assert [item.task_id for item in diff.improved] == ["t3"]
    assert diff.blocked is True
    assert diff.passed_before == 2
    assert diff.passed_after == 2


def test_regression_diff_allows_when_nothing_regressed() -> None:
    before = _result([_task_result("t1", CheckStatus.PASS)])
    after = _result([_task_result("t1", CheckStatus.PASS)])

    diff = regression_diff(before, after, before_attestation_id="a", after_attestation_id="b")

    assert diff.regressed == []
    assert diff.blocked is False


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _write_corpus(directory: Path, tasks: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo-corpus", "version": 1, "tasks": tasks}),
        encoding="utf-8",
    )
    return directory


def _setup(
    make_manifest: Callable[..., AgentWorkload], corpora_dir: Path
) -> tuple[RegistryService, CertificationService, CertificationCoordinator]:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
    )
    registry.create(
        make_manifest(
            name="repo-agent",
            certification={
                "benchmark_corpus": "corpus.yaml",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
            },
        )
    )
    engine = CertificationEngine(_policy(), clock=lambda: _FIXED_NOW)
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"att-{counter['n']}"

    service = CertificationService(
        engine,
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
        id_factory=_id,
    )
    coordinator = CertificationCoordinator(
        registry,
        service,
        InMemoryCertificationStore(),
        executor=ReferenceExecutor(),
        corpora_dir=corpora_dir,
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
    )
    return registry, service, coordinator


# --------------------------------------------------------------------------- #
# Coordinator + store
# --------------------------------------------------------------------------- #
def test_coordinator_certifies_and_records(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path, _TASKS)
    _, _, coordinator = _setup(make_manifest, corpora_dir)

    record = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)

    assert record.certification.status is CertificationStatus.PROVISIONAL
    assert record.attestation.workload_id == "repo-agent"
    assert record.benchmark_result.aggregate.total == 2
    assert record.benchmark_result.aggregate.passed == 2
    assert coordinator.store.get(record.certification.certification_id) is not None


def test_coordinator_promotes_to_certified(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path, _TASKS)
    registry, _, coordinator = _setup(make_manifest, corpora_dir)

    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    record = coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)

    assert record.certification.status is CertificationStatus.CERTIFIED
    assert registry.get("repo-agent").certification_status is CertificationStatus.CERTIFIED


def test_store_get_and_list_filters(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path, _TASKS)
    _, _, coordinator = _setup(make_manifest, corpora_dir)
    record = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)

    store = coordinator.store

    assert store.get(record.certification.certification_id) is not None
    assert store.get("missing") is None
    assert len(store.list(workload="repo-agent")) == 1
    assert len(store.list(status=CertificationStatus.PROVISIONAL)) == 1
    assert store.list(status=CertificationStatus.CERTIFIED) == []


def test_compare_certifications(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path, _TASKS)
    _, _, coordinator = _setup(make_manifest, corpora_dir)

    first = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    second = coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)

    diff = coordinator.compare(
        first.certification.certification_id, second.certification.certification_id
    )

    assert diff.blocked is False
    assert diff.before_attestation_id == first.attestation.attestation_id
    assert diff.after_attestation_id == second.attestation.attestation_id
