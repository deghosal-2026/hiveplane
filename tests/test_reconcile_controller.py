"""End-to-end controller tests: observe -> diff -> plan -> act -> record (M26-02)."""

from __future__ import annotations

from pathlib import Path

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.manifest import parse_manifest
from hiveplane.fleet.reconcile import ReconcileStatus
from hiveplane.reconcile.controller import ReconcileController
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.locking import InMemoryReconcileLock
from hiveplane.reconcile.models import ReconcileMode, ReconcileOutcome
from hiveplane.reconcile.observe import ServiceObserver
from hiveplane.reconcile.planner import Guardrails
from hiveplane.reconcile.source import SourceKind, SourceRef
from hiveplane.reconcile.store import InMemoryReconcileStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_WORKLOAD = """\
kind: workload
metadata: {name: agent-1, owner: platform-team, team: platform}
spec:
  runtime: {adapter: raw-worker, entrypoint: "examples.worker:run"}
  model:
    strategy: tiered
    identity: {provider: openai, family: gpt-4o, version: "2024-08-06"}
  certification:
    benchmark_corpus: corpora/agent-1/v1
    staging_threshold: 0.8
    production_threshold: 0.9
    status: uncertified
  budget: {per_run_usd: 0.5, per_day_usd: 5.0, per_team_usd: 50.0}
"""


def _stack(*, destructive: bool = False) -> tuple[
    RegistryService, InMemoryReconcileStore, ReconcileController
]:
    registry = RegistryService(InMemoryRegistryStore())
    store = InMemoryReconcileStore()
    observer = ServiceObserver(registry)
    executor = ActionExecutor(registry)
    controller = ReconcileController(
        store=store,
        observer=observer,
        executor=executor,
        lock=InMemoryReconcileLock(),
        guardrails=Guardrails(
            allow_destructive=destructive,
            allow_empty=destructive,
            require_destructive_confirmation=not destructive,
        ),
    )
    return registry, store, controller


def _source(path: Path, source_id: str = "git-main") -> SourceRef:
    return SourceRef(source_id=source_id, kind=SourceKind.DIRECTORY, path=str(path))


def _write(path: Path, content: str, name: str = "agent.yaml") -> None:
    (path / name).write_text(content)


def test_apply_registers_missing_workload_and_records(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    registry, store, controller = _stack()
    run = controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert run.outcome is ReconcileOutcome.APPLIED
    assert run.applied_count == 1
    assert registry.get("agent-1").name == "agent-1"
    assert store.list_specs("git-main")
    state = store.get_state("git-main")
    assert state is not None and state.status is ReconcileStatus.IN_SYNC
    assert [record.run_id for record in store.list_runs("git-main")] == [run.run_id]


def test_second_apply_is_idempotent(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    _, store, controller = _stack()
    first = controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    second = controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert first.outcome is ReconcileOutcome.APPLIED
    assert second.outcome is ReconcileOutcome.IN_SYNC
    assert second.actions == []
    assert len(store.list_runs("git-main")) == 2


def test_plan_mode_mutates_nothing(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    registry, store, controller = _stack()
    run = controller.reconcile(_source(tmp_path), mode=ReconcileMode.PLAN)
    assert run.outcome is ReconcileOutcome.PLANNED
    assert run.action_count == 1
    assert registry.list_workloads() == []
    assert store.list_specs() == []
    assert store.list_runs() == []
    assert store.get_state("git-main") is None


def test_invalid_desired_state_records_error_and_acts_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "kind: workload\nmetadata: {name: broken}\nspec: {}\n")
    registry, store, controller = _stack()
    run = controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert run.outcome is ReconcileOutcome.ERROR
    assert run.error is not None
    assert registry.list_workloads() == []
    assert store.list_specs() == []


def test_removed_workload_is_deregistered(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    registry, _, controller = _stack(destructive=True)
    controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    # Now the desired set no longer declares the workload.
    (tmp_path / "agent.yaml").unlink()
    run = controller.reconcile(
        _source(tmp_path), mode=ReconcileMode.APPLY, confirmed=True
    )
    assert run.outcome is ReconcileOutcome.APPLIED
    assert registry.list_workloads() == []


def test_unmanaged_workload_is_quarantined(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    registry, _, controller = _stack(destructive=True)
    registry.create(
        # Register a workload that is not declared by the source.
        parse_manifest(
            {
                "apiVersion": "hiveplane/v1",
                "kind": "AgentWorkload",
                "metadata": {"name": "rogue", "owner": "platform-team"},
                "spec": {
                    "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
                    "model": {
                        "strategy": "tiered",
                        "identity": {
                            "provider": "openai",
                            "family": "gpt-4o",
                            "version": "2024-08-06",
                        },
                    },
                    "certification": {
                        "benchmark_corpus": "corpora/x/v1",
                        "staging_threshold": 0.8,
                        "production_threshold": 0.9,
                        "status": "uncertified",
                    },
                    "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0, "per_team_usd": 50.0},
                },
            }
        )
    )
    run = controller.reconcile(
        _source(tmp_path), mode=ReconcileMode.APPLY, confirmed=True
    )
    assert run.outcome is ReconcileOutcome.APPLIED
    assert (
        registry.get("rogue").certification_status is CertificationStatus.QUARANTINED
    )


def test_lock_contention_skips_the_pass(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    registry, _, controller = _stack()
    lock = InMemoryReconcileLock()
    controller._lock = lock
    with lock.hold("git-main"):
        run = controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert run.outcome is ReconcileOutcome.BLOCKED
    assert registry.list_workloads() == []


def test_status_view_reports_last_reconcile(tmp_path: Path) -> None:
    _write(tmp_path, _WORKLOAD)
    _, _, controller = _stack()
    controller.reconcile(_source(tmp_path), mode=ReconcileMode.APPLY)
    view = controller.status("git-main")
    assert view.source_id == "git-main"
    assert view.status is ReconcileStatus.IN_SYNC
    assert view.last_revision
    assert view.open_drift == 0
    assert view.last_outcome is ReconcileOutcome.APPLIED
