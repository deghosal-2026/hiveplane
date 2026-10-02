"""Git/source change-detection tests: poll and webhook (M26-06)."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from hiveplane.reconcile.controller import ReconcileController
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.models import ReconcileMode, ReconcileOutcome
from hiveplane.reconcile.observe import ServiceObserver
from hiveplane.reconcile.source import (
    SourceKind,
    SourceRef,
    SourceWatcher,
    WebhookSignatureError,
    verify_webhook_signature,
)
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


def _stack() -> tuple[ReconcileController, SourceWatcher]:
    registry = RegistryService(InMemoryRegistryStore())
    controller = ReconcileController(
        store=InMemoryReconcileStore(),
        observer=ServiceObserver(registry),
        executor=ActionExecutor(registry),
    )
    return controller, SourceWatcher(controller)


def _source(path: Path) -> SourceRef:
    return SourceRef(source_id="git-main", kind=SourceKind.DIRECTORY, path=str(path))


def test_verify_webhook_signature_accepts_and_rejects() -> None:
    body = b'{"ref": "main"}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature("secret", body, signature) is True
    assert verify_webhook_signature("secret", body, "deadbeef") is False


def test_poll_reconciles_only_on_change(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(_WORKLOAD)
    _, watcher = _stack()
    first = watcher.poll(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert first is not None and first.outcome is ReconcileOutcome.APPLIED
    assert watcher.poll(_source(tmp_path), mode=ReconcileMode.APPLY) is None

    (tmp_path / "agent.yaml").write_text(_WORKLOAD.replace("per_day_usd: 5.0", "per_day_usd: 9.0"))
    second = watcher.poll(_source(tmp_path), mode=ReconcileMode.APPLY)
    assert second is not None and second.outcome is ReconcileOutcome.APPLIED


def test_webhook_verifies_signature_then_reconciles(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(_WORKLOAD)
    _, watcher = _stack()
    body = b'{"event": "push"}'
    with pytest.raises(WebhookSignatureError):
        watcher.webhook(_source(tmp_path), body, "bad-signature", "secret")

    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    run = watcher.webhook(_source(tmp_path), body, signature, "secret")
    assert run.outcome is ReconcileOutcome.APPLIED
