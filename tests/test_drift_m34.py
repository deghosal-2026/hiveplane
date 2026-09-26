"""Drift detection, auto-quarantine, reinstatement, and expiry tests (M34)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import JsonValue
from sqlalchemy import Engine

from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkTask,
    CertificationPolicy,
    Environment,
    EvalSummary,
    Severity,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.runner import ReferenceExecutor, TaskExecution
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import AdmissionContext
from hiveplane.core.workload import AgentWorkload
from hiveplane.drift.detector import DriftDetector
from hiveplane.drift.errors import (
    DriftNotConfiguredError,
    QuarantineNotFoundError,
    ReinstatementRefusedError,
)
from hiveplane.drift.models import (
    DriftAssessment,
    DriftSchedule,
    DriftVerdict,
    ExpiryState,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.notify import DriftNotifier
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.drift.store import InMemoryDriftStore, PostgresDriftStore
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from postgres import reset_database, seed_workload

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


class _Clock:
    """A mutable clock shared by the registry, service, and coordinator."""

    def __init__(self, now: datetime = _NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _ScriptedExecutor:
    """Passes every task except the ones in ``fail`` (deterministic)."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self._fail = fail or set()

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        if task.id in self._fail:
            return TaskExecution(
                output={}, actions=[], latency_ms=50, tokens=20, cost_usd=0.02,
                trace_id=f"trace-{task.id}",
            )
        return ReferenceExecutor().execute(task)


class _FakeCanceller:
    def __init__(self, runs: list[str] | None = None) -> None:
        self.runs = runs or []
        self.calls: list[str] = []

    def cancel_in_flight(self, workload: str) -> list[str]:
        self.calls.append(workload)
        return self.runs


class _FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[FanOutDestination, dict[str, JsonValue]]] = []

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
        self.messages.append((destination, message))


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


def _write_corpus(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo", "version": 1, "tasks": _TASKS}), encoding="utf-8"
    )
    return directory


def _setup(
    make_manifest: Callable[..., AgentWorkload], corpora_dir: Path, clock: _Clock
) -> tuple[RegistryService, CertificationService, InMemoryCertificationStore]:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(), clock=clock, attestation_public_key=public_key
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
        CertificationEngine(_policy(), clock=clock),
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=clock,
        id_factory=lambda: f"att-{next(counter)}",
    )
    return registry, service, InMemoryCertificationStore()


def _coordinator(
    registry: RegistryService,
    service: CertificationService,
    store: InMemoryCertificationStore,
    executor: object,
    corpora_dir: Path,
    clock: _Clock,
) -> CertificationCoordinator:
    return CertificationCoordinator(
        registry,
        service,
        store,
        executor=executor,  # type: ignore[arg-type]
        corpora_dir=corpora_dir,
        environment=_ENV,
        clock=clock,
    )


def _certify_baseline(coordinator: CertificationCoordinator) -> None:
    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)


def _summary(
    *, pass_rate: float, tasks_failed: int, tasks_passed: int, critical: int = 0
) -> EvalSummary:
    return EvalSummary(
        pass_rate=pass_rate,
        critical_failures=critical,
        p95_latency_ms=1000,
        tasks_passed=tasks_passed,
        tasks_failed=tasks_failed,
    )


def _assessment(workload: str = "repo-agent", *, exceeded: bool = True) -> DriftAssessment:
    return DriftAssessment(
        workload=workload,
        verdict=DriftVerdict.DRIFTED if exceeded else DriftVerdict.STABLE,
        pass_rate_before=1.0,
        pass_rate_after=0.5,
        pass_rate_delta=0.5,
        failed_before=0,
        failed_after=1,
        new_failures=1,
        critical_failures=1,
        threshold_pass_rate=0.1,
        max_new_failures=2,
        required_consecutive_failures=2,
        consecutive_failures=2,
        exceeded=exceeded,
        strong_signal=True,
        should_quarantine=exceeded,
        reason="behavioral drift confirmed",
        timestamp=_NOW,
    )


def _quarantine_record(workload: str = "repo-agent") -> QuarantineRecord:
    return QuarantineRecord(
        quarantine_id="quar-1",
        workload=workload,
        reason="behavioral drift confirmed",
        severity=Severity.CRITICAL,
        timestamp=_NOW,
    )


# --------------------------------------------------------------------------- #
# detector (M34-02, M34-08)
# --------------------------------------------------------------------------- #
def test_detector_stable_within_tolerance() -> None:
    detector = DriftDetector(clock=lambda: _NOW)
    result = detector.assess(
        workload="repo-agent",
        baseline=_summary(pass_rate=1.0, tasks_failed=0, tasks_passed=10),
        current=_summary(pass_rate=0.95, tasks_failed=1, tasks_passed=9),
    )
    assert result.verdict is DriftVerdict.STABLE
    assert result.exceeded is False
    assert result.should_quarantine is False
    assert "within tolerance" in result.reason


def test_detector_warns_then_confirms_on_consecutive_failures() -> None:
    detector = DriftDetector(clock=lambda: _NOW)
    baseline = _summary(pass_rate=1.0, tasks_failed=0, tasks_passed=20)
    current = _summary(pass_rate=0.85, tasks_failed=3, tasks_passed=17)
    first = detector.assess(
        workload="repo-agent", baseline=baseline, current=current, consecutive_failures=1
    )
    second = detector.assess(
        workload="repo-agent", baseline=baseline, current=current, consecutive_failures=2
    )
    assert first.verdict is DriftVerdict.WARNING
    assert first.exceeded is True and first.should_quarantine is False
    assert second.verdict is DriftVerdict.DRIFTED
    assert second.should_quarantine is True


def test_detector_strong_signal_quarantines_immediately() -> None:
    detector = DriftDetector(clock=lambda: _NOW)
    result = detector.assess(
        workload="repo-agent",
        baseline=_summary(pass_rate=1.0, tasks_failed=0, tasks_passed=10),
        current=_summary(pass_rate=0.5, tasks_failed=1, tasks_passed=1, critical=1),
        consecutive_failures=1,
    )
    assert result.strong_signal is True
    assert result.verdict is DriftVerdict.DRIFTED
    assert result.should_quarantine is True


def test_detector_respects_max_new_failures() -> None:
    detector = DriftDetector(max_new_failures=5, clock=lambda: _NOW)
    result = detector.assess(
        workload="repo-agent",
        baseline=_summary(pass_rate=1.0, tasks_failed=0, tasks_passed=30),
        current=_summary(pass_rate=0.95, tasks_failed=3, tasks_passed=27),
        consecutive_failures=1,
    )
    assert result.new_failures == 3
    assert result.exceeded is False
    assert result.should_quarantine is False


def test_detector_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        DriftDetector(threshold_pass_rate=2.0)
    with pytest.raises(ValueError):
        DriftDetector(required_consecutive_failures=0)
    with pytest.raises(ValueError):
        DriftDetector(strong_multiplier=0.5)


# --------------------------------------------------------------------------- #
# monitor + auto-quarantine (M34-02, M34-03, M34-04, M34-05, M34-08)
# --------------------------------------------------------------------------- #
def _monitor(
    registry: RegistryService,
    coordinator: CertificationCoordinator,
    *,
    store: InMemoryDriftStore | None = None,
    canceller: _FakeCanceller | None = None,
    transport: _FakeTransport | None = None,
    audit: InMemoryAuditLog | None = None,
) -> tuple[DriftMonitor, InMemoryDriftStore, QuarantineService]:
    store = store or InMemoryDriftStore()
    notifier = None
    if transport is not None:
        notifier = DriftNotifier(
            {FanOutType.WEBHOOK: transport},
            [FanOutDestination(type=FanOutType.WEBHOOK, url="http://hook")],
        )
    service = QuarantineService(
        registry,
        store,
        audit=audit,
        notifier=notifier,
        canceller=canceller,
        clock=lambda: _NOW,
        id_factory=lambda: "quar-1",
    )
    detector = DriftDetector(clock=lambda: _NOW)
    monitor = DriftMonitor(
        detector, store, coordinator, quarantine_service=service, clock=lambda: _NOW
    )
    return monitor, store, service


def test_stable_agent_is_never_falsely_quarantined(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(coordinator)
    monitor, drift_store, _ = _monitor(registry, coordinator)

    for _ in range(5):
        result = monitor.evaluate(
            "repo-agent", _summary(pass_rate=1.0, tasks_failed=0, tasks_passed=20)
        )
        assert result.verdict is DriftVerdict.STABLE

    assert registry.get("repo-agent").certification_status.value == "certified"
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True
    assert drift_store.list_quarantines() == []


def test_drifting_agent_is_quarantined_and_owner_notified(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(coordinator)
    transport = _FakeTransport()
    canceller = _FakeCanceller(["run-1", "run-2"])
    audit = InMemoryAuditLog()
    monitor, drift_store, _ = _monitor(
        registry, coordinator, canceller=canceller, transport=transport, audit=audit
    )

    warning = monitor.evaluate(
        "repo-agent", _summary(pass_rate=0.85, tasks_failed=3, tasks_passed=17)
    )
    assert warning.verdict is DriftVerdict.WARNING
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True

    drifted = monitor.evaluate(
        "repo-agent", _summary(pass_rate=0.85, tasks_failed=3, tasks_passed=17)
    )
    assert drifted.verdict is DriftVerdict.DRIFTED

    record = registry.get("repo-agent")
    assert record.certification_status.value == "quarantined"
    # Quarantine revokes production admission immediately.
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False

    history = drift_store.list_quarantines(workload="repo-agent")
    assert len(history) == 1
    quarantined = history[0]
    assert quarantined.status is QuarantineStatus.ACTIVE
    assert quarantined.reason == drifted.reason
    assert quarantined.cancelled_runs == ["run-1", "run-2"]
    assert quarantined.notified == ["http://hook"]
    assert canceller.calls == ["repo-agent"]
    assert transport.messages
    _, message = transport.messages[0]
    assert message["type"] == "certification.quarantined"
    assert message["workload_id"] == "repo-agent"
    evidence = cast("dict[str, object]", message["evidence"])
    assert evidence["consecutive_failures"] == 2
    assert any(entry.action == "workload.quarantined" for entry in audit.records())


def test_context_free_quarantine_when_cancel_disabled(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, _service, _store = _setup(make_manifest, corpora_dir, clock)
    drift_store = InMemoryDriftStore()
    service_quarantine = QuarantineService(
        registry,
        drift_store,
        canceller=_FakeCanceller(["run-1"]),
        cancel_in_flight=False,
        clock=lambda: _NOW,
        id_factory=lambda: "quar-1",
    )
    record = service_quarantine.quarantine("repo-agent", reason="manual")
    assert record.cancelled_runs == []
    # Idempotent while active.
    again = service_quarantine.quarantine("repo-agent", reason="manual")
    assert again.quarantine_id == record.quarantine_id
    assert len(drift_store.list_quarantines()) == 1
    assert service_quarantine.get("quar-1") is not None
    assert service_quarantine.get("missing") is None


def test_monitor_requires_certified_baseline(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    monitor, _, _ = _monitor(registry, coordinator)
    with pytest.raises(DriftNotConfiguredError):
        monitor.evaluate(
            "repo-agent", _summary(pass_rate=1.0, tasks_failed=0, tasks_passed=2)
        )


def test_monitor_probe_runs_a_fresh_benchmark(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(coordinator)
    monitor, _, _ = _monitor(registry, coordinator)
    result = monitor.probe_and_evaluate("repo-agent")
    assert result.workload == "repo-agent"
    assert result.verdict is DriftVerdict.STABLE


# --------------------------------------------------------------------------- #
# scheduler + expiry (M34-01, M34-07)
# --------------------------------------------------------------------------- #
def test_scheduler_cadence_and_due(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(coordinator)
    scheduler = DriftScheduler(registry, clock=clock)

    schedule = scheduler.schedule("repo-agent")
    assert schedule.interval_seconds == 14 * 86400
    assert schedule.last_certified_at == _NOW
    assert scheduler.due() == []

    clock.advance(14 * 86400)
    due = scheduler.due()
    assert [item.workload for item in due] == ["repo-agent"]
    assert due[0].overdue is True


def test_certification_expiry_blocks_admission(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(coordinator)
    scheduler = DriftScheduler(registry, clock=clock, renewal_window=3 * 86400)

    assert scheduler.expiry("repo-agent").state is ExpiryState.VALID

    clock.advance(12 * 86400)
    assert scheduler.expiry("repo-agent").state is ExpiryState.EXPIRING
    assert scheduler.expiries()[0].state is ExpiryState.EXPIRING

    clock.advance(3 * 86400)
    assert scheduler.expiry("repo-agent").state is ExpiryState.EXPIRED
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


# --------------------------------------------------------------------------- #
# reinstatement (M34-06)
# --------------------------------------------------------------------------- #
def test_reinstatement_requires_fresh_passing_certification(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(good)
    audit = InMemoryAuditLog()
    drift_store = InMemoryDriftStore()
    quarantine_service = QuarantineService(
        registry, drift_store, clock=lambda: _NOW, id_factory=lambda: "quar-1"
    )
    quarantine_service.quarantine("repo-agent", reason="drift", actor="operator")
    reinstatement = ReinstatementService(
        registry, drift_store, good, audit=audit, clock=lambda: _NOW
    )

    record = reinstatement.reinstate("repo-agent", operator="alice")

    assert record.status is QuarantineStatus.REINSTATED
    assert record.reinstated_by == "alice"
    assert registry.get("repo-agent").certification_status.value == "certified"
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True
    assert any(entry.action == "workload.reinstated" for entry in audit.records())


def test_reinstatement_refused_when_still_failing(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    bad = _coordinator(
        registry, service, store, _ScriptedExecutor(fail={"t2"}), corpora_dir, clock
    )
    _certify_baseline(good)
    drift_store = InMemoryDriftStore()
    quarantine_service = QuarantineService(
        registry, drift_store, clock=lambda: _NOW, id_factory=lambda: "quar-1"
    )
    quarantine_service.quarantine("repo-agent", reason="drift")
    reinstatement = ReinstatementService(
        registry, drift_store, bad, clock=lambda: _NOW
    )

    with pytest.raises(ReinstatementRefusedError):
        reinstatement.reinstate("repo-agent", operator="alice")

    assert drift_store.active_quarantine("repo-agent") is not None
    assert registry.get("repo-agent").certification_status.value == "quarantined"


def test_reinstatement_without_quarantine_raises(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    reinstatement = ReinstatementService(
        registry, InMemoryDriftStore(), good, clock=lambda: _NOW
    )
    with pytest.raises(QuarantineNotFoundError):
        reinstatement.reinstate("repo-agent", operator="alice")


# --------------------------------------------------------------------------- #
# store (M34-05)
# --------------------------------------------------------------------------- #
def test_postgres_drift_store_round_trip(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    seed_workload(pg_engine, "repo-agent")
    store = PostgresDriftStore(pg_engine)

    store.add_assessment(_assessment())
    assert store.consecutive_failures("repo-agent") == 1
    assert store.list_assessments("repo-agent", limit=1)[0].workload == "repo-agent"

    store.add_quarantine(_quarantine_record())
    fetched = store.get_quarantine("quar-1")
    assert fetched is not None and fetched.reason == "behavioral drift confirmed"
    reinstated = fetched.model_copy(update={"status": QuarantineStatus.REINSTATED})
    store.save_quarantine(reinstated)
    refetched = store.get_quarantine("quar-1")
    assert refetched is not None and refetched.status is QuarantineStatus.REINSTATED
    assert store.active_quarantine("repo-agent") is None
    assert len(store.list_quarantines(workload="repo-agent")) == 1

    schedule = DriftSchedule(
        schedule_id="schedule-repo-agent",
        workload="repo-agent",
        interval_seconds=14 * 86400,
        next_re_cert_run=_NOW,
    )
    store.save_schedule(schedule)
    assert store.list_schedules()[0].workload == "repo-agent"
    store.clear()
    assert store.list_quarantines() == []


def test_consecutive_failures_resets_on_stable() -> None:
    store = InMemoryDriftStore()
    store.add_assessment(_assessment())
    store.add_assessment(_assessment())
    assert store.consecutive_failures("repo-agent") == 2
    store.add_assessment(_assessment(exceeded=False))
    assert store.consecutive_failures("repo-agent") == 0


# --------------------------------------------------------------------------- #
# notification, scheduler, and tenant-scope edges (M34 coverage)
# --------------------------------------------------------------------------- #
def test_notifier_disabled_and_without_transport() -> None:
    transport = _FakeTransport()
    disabled = DriftNotifier({FanOutType.WEBHOOK: transport}, enabled=False)
    assert disabled.enabled is False
    assert disabled.notify_quarantine(_quarantine_record()) == []

    no_destinations = DriftNotifier({FanOutType.WEBHOOK: transport})
    assert no_destinations.notify_quarantine(_quarantine_record()) == []

    missing_transport = DriftNotifier(
        {}, [FanOutDestination(type=FanOutType.WEBHOOK, url="http://hook")]
    )
    assert missing_transport.notify_quarantine(_quarantine_record()) == []


def test_compose_message_without_assessment() -> None:
    from hiveplane.drift.notify import compose_quarantine_message

    message = compose_quarantine_message(_quarantine_record(), owner="team")
    assert message["owner"] == "team"
    assert message["pass_rate_after"] is None


def test_detector_rejects_negative_max_new_failures() -> None:
    with pytest.raises(ValueError):
        DriftDetector(max_new_failures=-1)


def test_detector_severity_mapping() -> None:
    detector = DriftDetector(clock=lambda: _NOW)
    critical = detector.assess(
        workload="repo-agent",
        baseline=_summary(pass_rate=1.0, tasks_failed=0, tasks_passed=10),
        current=_summary(pass_rate=0.5, tasks_failed=1, tasks_passed=1, critical=1),
    )
    assert detector.severity_for(critical) is Severity.CRITICAL
    warning = detector.assess(
        workload="repo-agent",
        baseline=_summary(pass_rate=1.0, tasks_failed=0, tasks_passed=20),
        current=_summary(pass_rate=0.85, tasks_failed=3, tasks_passed=17),
    )
    assert detector.severity_for(warning) is Severity.WARNING


def test_quarantine_defaults_to_warning_without_assessment(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, _service, _store = _setup(make_manifest, corpora_dir, clock)
    store = InMemoryDriftStore()
    service = QuarantineService(
        registry, store, clock=lambda: _NOW, id_factory=lambda: "quar-1"
    )
    record = service.quarantine("repo-agent", reason="manual")
    assert record.severity is Severity.WARNING
    assert service.list(workload="repo-agent") == [record]


def test_scheduler_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        DriftScheduler(registry=None, default_interval=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DriftScheduler(registry=None, renewal_window=-1)  # type: ignore[arg-type]


def test_scheduler_uncertified_workload_uses_created_at(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    clock = _Clock()
    registry = RegistryService(InMemoryRegistryStore(), clock=clock)
    registry.create(make_manifest(name="plain-agent", certification=None))
    scheduler = DriftScheduler(registry, clock=clock)

    schedule = scheduler.schedule("plain-agent")
    assert schedule.last_certified_at is None
    assert scheduler.expiry("plain-agent").state is ExpiryState.UNKNOWN
    assert scheduler.expiries()[0].state is ExpiryState.UNKNOWN


def test_scheduler_exposes_renewal_window() -> None:
    scheduler = DriftScheduler(registry=None, renewal_window=42)  # type: ignore[arg-type]
    assert scheduler.renewal_window == 42


def test_notifier_swallows_transport_errors() -> None:
    class _Boom:
        def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
            raise RuntimeError("boom")

    notifier = DriftNotifier(
        {FanOutType.WEBHOOK: _Boom()},
        [FanOutDestination(type=FanOutType.WEBHOOK, url="http://hook")],
    )
    assert notifier.notify_quarantine(_quarantine_record()) == []


def test_quarantine_uses_assessment_severity(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, _service, _store = _setup(make_manifest, corpora_dir, clock)
    store = InMemoryDriftStore()
    service = QuarantineService(
        registry, store, clock=lambda: _NOW, id_factory=lambda: "quar-1"
    )
    record = service.quarantine(
        "repo-agent", reason="drift", assessment=_assessment()
    )
    assert record.severity is Severity.CRITICAL


def test_reinstatement_refused_when_not_quarantined(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    drift_store = InMemoryDriftStore()
    drift_store.add_quarantine(_quarantine_record())
    reinstatement = ReinstatementService(
        registry, drift_store, good, clock=lambda: _NOW
    )
    with pytest.raises(ReinstatementRefusedError):
        reinstatement.reinstate("repo-agent", operator="alice")


def test_reinstatement_succeeds_without_audit(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    good = _coordinator(registry, service, store, ReferenceExecutor(), corpora_dir, clock)
    _certify_baseline(good)
    drift_store = InMemoryDriftStore()
    quarantine_service = QuarantineService(
        registry, drift_store, clock=lambda: _NOW, id_factory=lambda: "quar-1"
    )
    quarantine_service.quarantine("repo-agent", reason="drift")
    reinstatement = ReinstatementService(
        registry, drift_store, good, clock=lambda: _NOW
    )
    record = reinstatement.reinstate("repo-agent", operator="alice")
    assert record.reinstated_by == "alice"



def test_memory_store_tenant_scoping() -> None:
    from hiveplane.tenancy import TenantContext

    store = InMemoryDriftStore()
    acme = TenantContext(tenant_id="acme")
    record = _quarantine_record().model_copy(update={"tenant_id": "acme"})
    assessment = _assessment().model_copy(update={"tenant_id": "acme"})
    schedule = DriftSchedule(
        schedule_id="s1",
        workload="repo-agent",
        interval_seconds=1,
        next_re_cert_run=_NOW,
        tenant_id="acme",
    )

    store.add_quarantine(record, ctx=acme)
    store.add_assessment(assessment, ctx=acme)
    store.save_schedule(schedule, ctx=acme)

    assert store.get_quarantine("quar-1", ctx=acme) is not None
    assert store.get_quarantine("quar-1") is None
    assert store.list_quarantines() == []
    assert store.list_quarantines(ctx=acme) == [record]
    assert store.list_quarantines(status=QuarantineStatus.REINSTATED, ctx=acme) == []
    assert store.active_quarantine("repo-agent", ctx=acme) is not None
    assert store.consecutive_failures("repo-agent", ctx=acme) == 1
    assert store.list_assessments("repo-agent", limit=5, ctx=acme)[0].workload == "repo-agent"
    assert store.list_schedules(ctx=acme)[0].schedule_id == "s1"
    assert store.list_schedules() == []
    store.clear()
    assert store.list_quarantines(ctx=acme) == []


def test_postgres_drift_store_filters_and_tenant_scope(pg_engine: Engine) -> None:
    from hiveplane.tenancy import TenantContext, TenantScopeError

    reset_database(pg_engine)
    seed_workload(pg_engine, "repo-agent")
    seed_workload(pg_engine, "acme-agent", tenant_id="acme")
    store = PostgresDriftStore(pg_engine)
    acme = TenantContext(tenant_id="acme")

    store.add_quarantine(_quarantine_record())
    assert store.list_quarantines(status=QuarantineStatus.ACTIVE)[0].quarantine_id == "quar-1"
    assert store.list_quarantines(status=QuarantineStatus.REINSTATED) == []
    assert store.list_quarantines(ctx=acme) == []
    assert store.get_quarantine("quar-1", ctx=acme) is None

    other = _quarantine_record("acme-agent").model_copy(
        update={"tenant_id": "acme", "quarantine_id": "quar-2"}
    )
    store.add_quarantine(other, ctx=acme)
    with pytest.raises(TenantScopeError):
        store.save_quarantine(
            other.model_copy(update={"tenant_id": "other"}),
            ctx=TenantContext(tenant_id="other"),
        )

    store.add_assessment(_assessment())
    assert store.list_assessments("repo-agent", limit=1)[0].workload == "repo-agent"
    assert store.consecutive_failures("repo-agent") == 1

    schedule = DriftSchedule(
        schedule_id="s1",
        workload="repo-agent",
        interval_seconds=3600,
        next_re_cert_run=_NOW,
    )
    store.save_schedule(schedule)
    store.save_schedule(schedule.model_copy(update={"status": "due"}))
    assert store.list_schedules()[0].status == "due"
    with pytest.raises(TenantScopeError):
        store.save_schedule(
            schedule.model_copy(update={"tenant_id": "other"}),
            ctx=TenantContext(tenant_id="other"),
        )
    store.clear()
