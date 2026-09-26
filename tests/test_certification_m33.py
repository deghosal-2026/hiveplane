"""Regression diff, baseline selection, and report tests (M33, #226-#233)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import yaml

from hiveplane.certification.diff import build_regression_report, regression_diff
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkCorpus,
    BenchmarkResult,
    BenchmarkTask,
    BenchmarkTaskResult,
    CertificationPolicy,
    CheckStatus,
    Environment,
    RegressionReport,
    Severity,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.promotion import PromotionGate
from hiveplane.certification.promotion_store import InMemoryPromotionStore
from hiveplane.certification.runner import ReferenceExecutor, TaskExecution
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)
_TASKS = [
    {
        "id": "t1",
        "name": "classify low risk",
        "check": {"type": "exact_match", "field": "risk", "value": "low"},
    },
    {
        "id": "t2",
        "name": "flag risky change",
        "critical": True,
        "check": {"type": "action_audit", "required_actions": ["request_changes"]},
    },
]


class _ScriptedExecutor:
    """Passes every task except the ones in ``fail`` (deterministic)."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self._fail = fail or set()

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        task_id = task.id
        if task_id in self._fail:
            return TaskExecution(
                output={}, actions=[], latency_ms=50, tokens=20, cost_usd=0.02,
                trace_id=f"trace-{task_id}",
            )
        return ReferenceExecutor().execute(task)


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


def _task_result(
    task_id: str, status: CheckStatus, *, critical: bool = False, latency: int = 1,
    tokens: int = 0, cost: float = 0.0, trace: str | None = None,
) -> BenchmarkTaskResult:
    return BenchmarkTaskResult(
        task_id=task_id, status=status, latency_ms=latency, tokens=tokens,
        cost_usd=cost, critical=critical, trace_id=trace,
    )


def _result(tasks: list[BenchmarkTaskResult], *, run_id: str = "br-1") -> BenchmarkResult:
    passed = sum(1 for task in tasks if task.status is CheckStatus.PASS)
    return BenchmarkResult(
        benchmark_run_id=run_id,
        workload_id="repo-agent",
        manifest_version=1,
        corpus_id="corpus",
        corpus_version=1,
        model_identity="m",
        environment=_ENV,
        started_at=_NOW,
        finished_at=_NOW,
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


def _catalog() -> dict[str, BenchmarkTask]:
    corpus = BenchmarkCorpus.model_validate({"id": "corpus", "version": 1, "tasks": _TASKS})
    return {task.id: task for task in corpus.tasks}


# --------------------------------------------------------------------------- #
# diff engine (M33-01, M33-03, M33-04)
# --------------------------------------------------------------------------- #
def test_seeded_regression_is_pinpointed_with_metrics_and_replay() -> None:
    before = _result([_task_result("t1", CheckStatus.PASS), _task_result("t2", CheckStatus.PASS)])
    after = _result(
        [
            _task_result("t1", CheckStatus.PASS),
            _task_result(
                "t2", CheckStatus.FAIL, critical=True, latency=50, tokens=20, cost=0.02,
                trace="trace-t2",
            ),
        ]
    )
    diff = regression_diff(
        before, after, before_attestation_id="att-1", after_attestation_id="att-2",
        task_catalog=_catalog(),
    )

    assert diff.blocked is True
    assert diff.severity is Severity.CRITICAL
    assert diff.critical_regressions == ["t2"]
    assert [d.task_id for d in diff.regressed] == ["t2"]
    delta = diff.regressed[0]
    assert delta.severity is Severity.CRITICAL and delta.critical is True
    assert delta.latency_delta_ms == 49  # 50 - 1 (t2 prior latency 1)
    assert delta.tokens_delta == 20
    assert delta.cost_delta_usd == pytest.approx(0.02)
    assert delta.replay is not None and delta.replay.replayable is True
    assert delta.replay.trace_id == "trace-t2"
    assert delta.replay.replay_ref == "certification:att-2/task:t2"
    assert "t2" in diff.summary


def test_non_critical_regression_is_a_warning() -> None:
    before = _result([_task_result("t1", CheckStatus.PASS)])
    after = _result([_task_result("t1", CheckStatus.FAIL)])
    diff = regression_diff(
        before, after, before_attestation_id="a", after_attestation_id="b"
    )
    assert diff.severity is Severity.WARNING
    assert diff.critical_regressions == []
    assert diff.regressed[0].replay is not None
    assert diff.regressed[0].replay.replayable is False  # no catalog supplied


def test_identical_results_yield_empty_diff() -> None:
    tasks = [_task_result("t1", CheckStatus.PASS), _task_result("t2", CheckStatus.PASS)]
    diff = regression_diff(
        _result(tasks, run_id="a"),
        _result(tasks, run_id="b"),
        before_attestation_id="a",
        after_attestation_id="b",
    )
    assert diff.blocked is False
    assert diff.severity is Severity.NONE
    assert diff.regressed == [] and diff.improved == []
    assert "No regressions" in diff.summary


def test_fail_to_pass_is_an_improvement() -> None:
    before = _result([_task_result("t1", CheckStatus.FAIL)])
    after = _result([_task_result("t1", CheckStatus.PASS)])
    diff = regression_diff(before, after, before_attestation_id="a", after_attestation_id="b")
    assert [d.task_id for d in diff.improved] == ["t1"]
    assert diff.blocked is False


def test_removed_passing_task_blocks() -> None:
    before = _result([_task_result("t1", CheckStatus.PASS), _task_result("t2", CheckStatus.PASS)])
    after = _result([_task_result("t1", CheckStatus.PASS)])
    diff = regression_diff(before, after, before_attestation_id="a", after_attestation_id="b")
    assert diff.blocked is True and diff.removed == ["t2"]
    assert any("removed" in warning for warning in diff.warnings)


def test_regression_report_has_machine_and_human_parts() -> None:
    before = _result([_task_result("t1", CheckStatus.PASS)])
    after = _result([_task_result("t1", CheckStatus.FAIL)])
    diff = regression_diff(before, after, before_attestation_id="a", after_attestation_id="b")
    report = build_regression_report(diff, generated_at=_NOW)
    assert isinstance(report, RegressionReport)
    machine = report.machine_report
    assert machine.get("blocked") is True
    regressed = cast("list[dict[str, object]]", machine.get("regressed"))
    assert regressed[0]["task_id"] == "t1"
    assert "t1" in report.human_report


# --------------------------------------------------------------------------- #
# coordinator: baseline + report attachment (M33-02, M33-05)
# --------------------------------------------------------------------------- #
def _write_corpus(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo", "version": 1, "tasks": _TASKS}), encoding="utf-8"
    )
    return directory


def _setup(
    make_manifest: Callable[..., AgentWorkload], corpora_dir: Path
) -> tuple[RegistryService, CertificationService, InMemoryCertificationStore]:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(), clock=lambda: _NOW, attestation_public_key=public_key
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
    counter = itertools.count(1)
    service = CertificationService(
        CertificationEngine(_policy(), clock=lambda: _NOW),
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=lambda: _NOW,
        id_factory=lambda: f"att-{next(counter)}",
    )
    return registry, service, InMemoryCertificationStore()


def _coordinator(
    registry: RegistryService,
    service: CertificationService,
    store: InMemoryCertificationStore,
    executor: object,
    corpora_dir: Path,
) -> CertificationCoordinator:
    return CertificationCoordinator(
        registry,
        service,
        store,
        executor=executor,  # type: ignore[arg-type]
        corpora_dir=corpora_dir,
        environment=_ENV,
        clock=lambda: _NOW,
    )


def test_recertification_attaches_regression_report(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)
    bad = _coordinator(registry, service, store, _ScriptedExecutor(fail={"t2"}), corpora_dir)

    good.certify("repo-agent", target_context=TargetContext.STAGING)
    record = bad.certify("repo-agent", target_context=TargetContext.STAGING)

    report = record.regression_report
    assert report is not None
    assert report.severity is Severity.CRITICAL
    assert report.blocked is True
    assert report.machine_report.get("critical_regressions") == ["t2"]
    assert "t2" in report.human_report
    stored = store.get(record.record_id)
    assert stored is not None and stored.regression_report is not None


def test_compare_to_baseline_selects_last_certified(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)

    first = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    second = coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)

    diff = coordinator.compare_to_baseline("repo-agent", second.record_id)

    assert diff.baseline_attestation_id == first.attestation.attestation_id
    assert diff.blocked is False
    assert diff.severity is Severity.NONE
    # Replay frames are populated from the loaded corpus.
    assert diff.regressed == []


def test_compare_to_baseline_is_deterministic(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)
    first = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    second = coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)

    one = coordinator.compare_to_baseline("repo-agent", second.record_id)
    two = coordinator.compare_to_baseline(
        "repo-agent", second.record_id, baseline_id=first.attestation.attestation_id
    )
    assert one.model_dump() == two.model_dump()


def test_compare_to_baseline_without_baseline_raises(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    from hiveplane.certification.errors import CertificationNotFoundError

    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)
    only = coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    with pytest.raises(CertificationNotFoundError):
        coordinator.compare_to_baseline("repo-agent", only.record_id)


def test_critical_regression_feeds_promotion_refusal(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    registry, service, store = _setup(make_manifest, corpora_dir)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir)
    bad = _coordinator(registry, service, store, _ScriptedExecutor(fail={"t2"}), corpora_dir)

    good.certify("repo-agent", target_context=TargetContext.STAGING)
    gate = PromotionGate(
        registry, coordinator=bad, store=InMemoryPromotionStore(), clock=lambda: _NOW
    )
    _, promotion = gate.recertify_and_promote("repo-agent", operator="op")

    assert promotion.promoted is False
    assert "critical regression" in (promotion.refusal_reason or "")
