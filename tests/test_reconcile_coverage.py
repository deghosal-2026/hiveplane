"""Comprehensive edge-path tests for the reconcile modules (M26-TEST).

These exercise the error, guardrail, and fallback paths that the focused unit
tests do not: loader failures, blocked/failed outcomes, missing deltas, and the
Postgres store's update/limit branches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from hiveplane.core.manifest import parse_manifest
from hiveplane.fleet.reconcile import (
    DesiredSpec,
    DriftRecord,
    DriftResolution,
    ReconcileState,
    ReconcileStatus,
    SpecKind,
    SpecSource,
)
from hiveplane.policy.models import PolicyPack
from hiveplane.reconcile.conflict import ConflictPolicy
from hiveplane.reconcile.controller import (
    ReconcileController,
    _outcome,
    _state_status,
)
from hiveplane.reconcile.differ import (
    Delta,
    DeltaKind,
    Differ,
    DiffResult,
)
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.loader import (
    DesiredStateLoader,
    GitSource,
    LoaderError,
    authenticated_url,
)
from hiveplane.reconcile.models import (
    ActionClass,
    ActionKind,
    ActionResult,
    ActionStatus,
    DesiredSet,
    ReconcileAction,
    ReconcileMode,
    ReconcileOutcome,
    ReconcileRun,
)
from hiveplane.reconcile.observe import ObservedState, ServiceObserver
from hiveplane.reconcile.planner import Guardrails
from hiveplane.reconcile.source import SourceKind, SourceRef
from hiveplane.reconcile.store import InMemoryReconcileStore, PostgresReconcileStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from postgres import reset_database

_NOW = datetime(2026, 1, 1, tzinfo=UTC)

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


# --------------------------------------------------------------------------- #
# Loader edge paths
# --------------------------------------------------------------------------- #
def test_load_directory_rejects_non_directory(tmp_path: Path) -> None:
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path / "missing", source_id="s")


def test_load_directory_rejects_bad_yaml(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("kind: [unclosed")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="s")


def test_load_directory_rejects_non_mapping_document(tmp_path: Path) -> None:
    (tmp_path / "list.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="s")


def test_load_directory_rejects_missing_metadata_name(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("kind: workload\nmetadata: {}\nspec: {}\n")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="s")


def test_load_directory_rejects_missing_spec(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("kind: workload\nmetadata: {name: a}\n")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="s")


def test_load_directory_rejects_invalid_policy(tmp_path: Path) -> None:
    (tmp_path / "p.yaml").write_text(
        "kind: policy_pack\nmetadata: {name: p, team: platform}\nspec: {}\n"
    )
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="s")


def test_load_directory_accepts_trigger_and_budget(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(
        "kind: trigger\nmetadata: {name: t}\nspec: {source: github}\n"
    )
    (tmp_path / "b.yaml").write_text(
        "kind: budget\nmetadata: {name: b}\nspec: {periods: {day: 1}}\n"
    )
    desired = DesiredStateLoader().load_directory(tmp_path, source_id="s")
    assert desired.names(SpecKind.TRIGGER) == ["t"]
    assert desired.names(SpecKind.BUDGET) == ["b"]


def test_load_directory_empty_is_a_valid_empty_revision(tmp_path: Path) -> None:
    desired = DesiredStateLoader().load_directory(tmp_path, source_id="s")
    assert desired.specs == []
    assert desired.revision


def test_load_git_requires_resolver_for_auth(tmp_path: Path) -> None:
    source = GitSource(
        url="https://example.test/fleet.git", auth_secret_ref="git/token"
    )
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_git(source, source_id="s")


def test_load_git_uses_resolver_for_auth(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    resolved: list[str] = []

    def resolver(ref: str) -> str:
        resolved.append(ref)
        return "tok"

    source = GitSource(url=str(repo_path), auth_secret_ref="git/token")
    desired = DesiredStateLoader().load_git(
        source, source_id="s", secret_resolver=resolver
    )
    assert resolved == ["git/token"]
    assert desired.names(SpecKind.WORKLOAD) == ["agent-1"]


def test_load_git_reports_clone_failure() -> None:
    source = GitSource(url="https://invalid.example.test/nope.git")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_git(source, source_id="s")


def test_authenticated_url_leaves_non_http_untouched() -> None:
    source = GitSource(url="/local/path/repo")
    assert authenticated_url(source, "tok") == "/local/path/repo"


def _init_repo(path: Path) -> str:
    import subprocess

    path.mkdir()
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "agent.yaml").write_text(_WORKLOAD)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


# --------------------------------------------------------------------------- #
# Source edge paths
# --------------------------------------------------------------------------- #
def test_directory_source_requires_path() -> None:
    source = SourceRef(source_id="s", kind=SourceKind.DIRECTORY)
    with pytest.raises(LoaderError):
        source.load(DesiredStateLoader())


def test_git_source_requires_repository() -> None:
    source = SourceRef(source_id="s", kind=SourceKind.GIT)
    with pytest.raises(LoaderError):
        source.load(DesiredStateLoader())


# --------------------------------------------------------------------------- #
# Planner / controller outcome branches
# --------------------------------------------------------------------------- #
def test_outcome_and_state_status_branches() -> None:
    failed = ActionResult(
        action_id="a",
        kind=ActionKind.REGISTER_WORKLOAD,
        object_kind=SpecKind.WORKLOAD,
        object_ref="w",
        status=ActionStatus.FAILED,
    )
    blocked = failed.model_copy(update={"status": ActionStatus.BLOCKED})
    applied = failed.model_copy(update={"status": ActionStatus.APPLIED})
    assert _outcome(False, [failed]) is ReconcileOutcome.ERROR
    assert _outcome(False, [blocked]) is ReconcileOutcome.BLOCKED
    assert _outcome(False, [applied]) is ReconcileOutcome.APPLIED
    assert _outcome(False, [failed.model_copy(update={"status": ActionStatus.SKIPPED})]) is (
        ReconcileOutcome.IN_SYNC
    )
    assert _state_status(DiffResult(), [failed]) is ReconcileStatus.ERROR
    assert _state_status(DiffResult(), [blocked]) is ReconcileStatus.DRIFTED
    unresolved = DriftRecord(
        drift_id="d",
        object_ref="w",
        field="spec.x",
        detected_at=_NOW,
    )
    assert _state_status(DiffResult(drifts=[unresolved]), []) is ReconcileStatus.DRIFTED


def _controller() -> tuple[RegistryService, InMemoryReconcileStore, ReconcileController]:
    registry = RegistryService(InMemoryRegistryStore())
    store = InMemoryReconcileStore()
    controller = ReconcileController(
        store=store,
        observer=ServiceObserver(registry),
        executor=ActionExecutor(registry),
        guardrails=Guardrails(allow_destructive=False),
    )
    return registry, store, controller


def test_destructive_action_is_blocked_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(_WORKLOAD)
    registry, store, controller = _controller()
    registry.create(
        parse_manifest(
            {
                "apiVersion": "hiveplane/v1",
                "kind": "AgentWorkload",
                "metadata": {"name": "rogue", "owner": "platform-team"},
                "spec": {
                    "runtime": {
                        "adapter": "raw-worker",
                        "entrypoint": "examples.worker:run",
                    },
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
                    "budget": {
                        "per_run_usd": 0.5,
                        "per_day_usd": 5.0,
                        "per_team_usd": 50.0,
                    },
                },
            }
        )
    )
    (tmp_path / "agent.yaml").unlink()
    source = SourceRef(source_id="s", kind=SourceKind.DIRECTORY, path=str(tmp_path))
    run = controller.reconcile(source, mode=ReconcileMode.APPLY)
    assert run.outcome is ReconcileOutcome.BLOCKED
    assert run.blocked_count == 1
    state = store.get_state("s")
    assert state is not None
    assert state.status is ReconcileStatus.DRIFTED


def test_status_before_any_reconcile_is_pending() -> None:
    _, _, controller = _controller()
    view = controller.status("never")
    assert view.status is ReconcileStatus.PENDING
    assert view.last_revision is None
    assert view.last_outcome is None


# --------------------------------------------------------------------------- #
# Executor edge paths
# --------------------------------------------------------------------------- #
def _action(kind: ActionKind) -> ReconcileAction:
    return ReconcileAction(
        action_id="a",
        kind=kind,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        action_class=ActionClass.SOFT,
        summary="x",
    )


def test_register_without_desired_payload_fails() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    result = ActionExecutor(registry).execute(
        _action(ActionKind.REGISTER_WORKLOAD), DiffResult()
    )
    assert result.status is ActionStatus.FAILED


def test_update_without_payload_fails() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    delta = Delta(
        kind=DeltaKind.UPDATE, object_kind=SpecKind.WORKLOAD, object_ref="agent-1"
    )
    result = ActionExecutor(registry).execute(
        _action(ActionKind.UPDATE_WORKLOAD), DiffResult(deltas=[delta])
    )
    assert result.status is ActionStatus.FAILED


def test_recertify_missing_workload_fails() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    result = ActionExecutor(registry).execute(
        _action(ActionKind.RECERTIFY_WORKLOAD), DiffResult()
    )
    assert result.status is ActionStatus.FAILED


def test_enforce_policy_with_delta_but_no_desired_is_skipped() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    delta = Delta(
        kind=DeltaKind.ENFORCE_POLICY,
        object_kind=SpecKind.POLICY,
        object_ref="p",
    )
    action = _action(ActionKind.ENFORCE_POLICY_VERSION).model_copy(
        update={"object_kind": SpecKind.POLICY, "object_ref": "p"}
    )
    result = ActionExecutor(
        registry, policy_enforcer=lambda spec: None
    ).execute(action, DiffResult(deltas=[delta]))
    assert result.status is ActionStatus.SKIPPED


# --------------------------------------------------------------------------- #
# Differ / observer fallbacks
# --------------------------------------------------------------------------- #
def test_policy_without_metadata_version_is_enforced() -> None:
    spec = DesiredSpec(
        spec_id="policy/p",
        source_id="s",
        source=SpecSource.GIT,
        kind=SpecKind.POLICY,
        name="p",
        revision="r",
        content_hash="c" * 64,
        spec={"metadata": {"name": "p"}},
        updated_at=_NOW,
    )
    desired = DesiredSet(
        source_id="s",
        source=SpecSource.GIT,
        revision="r",
        specs=[spec],
        generated_at=_NOW,
    )
    result = Differ(ConflictPolicy()).diff(
        desired,
        ObservedState(policy_versions={"p": "1"}),
        run_id="rr",
        tenant_id="default",
        detected_at=_NOW,
    )
    assert [delta.kind for delta in result.deltas] == [DeltaKind.ENFORCE_POLICY]


def test_observer_reads_policy_versions() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    from hiveplane.policy.packs import InMemoryPolicyPackStore

    packs = InMemoryPolicyPackStore()
    packs.save(
        PolicyPack.model_validate(
            {
                "apiVersion": "hiveplane/v1",
                "kind": "PolicyPack",
                "metadata": {"name": "baseline", "team": "platform", "version": "2"},
                "spec": {},
            }
        )
    )
    snapshot = ServiceObserver(registry, packs).snapshot()
    assert snapshot.policy_versions == {"baseline": "2"}


# --------------------------------------------------------------------------- #
# Postgres store update/limit branches
# --------------------------------------------------------------------------- #
def _spec(name: str, source_id: str = "git-main") -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"workload/{name}",
        source_id=source_id,
        source=SpecSource.GIT,
        kind=SpecKind.WORKLOAD,
        name=name,
        revision="r",
        content_hash="c" * 64,
        spec={},
        updated_at=_NOW,
    )


def test_postgres_store_update_and_limit_branches(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReconcileStore(pg_engine)
    store.replace_source_specs("git-main", [_spec("a"), _spec("b")])
    store.replace_source_specs("git-other", [_spec("c", source_id="git-other")])
    assert [s.name for s in store.list_specs("git-main")] == ["a", "b"]
    assert [s.name for s in store.list_specs()] == ["a", "b", "c"]

    store.save_state(
        ReconcileState(
            source_id="git-main",
            source=SpecSource.GIT,
            last_revision="r1",
            last_observed_hash="h1",
            last_reconcile_at=_NOW,
            status=ReconcileStatus.PENDING,
        )
    )
    store.save_state(
        ReconcileState(
            source_id="git-main",
            source=SpecSource.GIT,
            last_revision="r2",
            last_observed_hash="h2",
            last_reconcile_at=_NOW,
            status=ReconcileStatus.IN_SYNC,
        )
    )
    state = store.get_state("git-main")
    assert state is not None
    assert state.last_revision == "r2"
    assert [s.source_id for s in store.list_states()] == ["git-main"]

    drift = DriftRecord(
        drift_id="d1", object_ref="a", field="spec.x", detected_at=_NOW
    )
    store.add_drift(drift)
    store.save_drift(drift.model_copy(update={"resolution": DriftResolution.OBSERVED_WINS}))
    assert store.list_drift(object_ref="a")[0].resolution is DriftResolution.OBSERVED_WINS
    assert store.list_drift(reconcile_run_id="none") == []

    for index in range(3):
        store.add_run(
            ReconcileRun(
                run_id=f"rr-{index}",
                source_id="git-main",
                source=SpecSource.GIT,
                revision="r",
                mode=ReconcileMode.APPLY,
                started_at=_NOW,
                outcome=ReconcileOutcome.APPLIED,
            )
        )
    assert [run.run_id for run in store.list_runs(limit=2)] == ["rr-1", "rr-2"]

    store.clear()
    assert store.list_specs() == []
    assert store.list_states() == []
    assert store.list_drift() == []
    assert store.list_runs() == []
